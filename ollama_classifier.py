"""
ollama_classifier.py - Unified email classifier supporting:
  - Local Ollama  (provider: "ollama")
  - OpenAI        (provider: "openai")
  - Google Gemini (provider: "gemini")
  - Anthropic     (provider: "anthropic")
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

import requests

import config as cfg

logger = logging.getLogger(__name__)


@dataclass
class ClassificationResult:
    company: str
    role: str
    status: str
    raw_response: str
    is_job_email: bool


_SYSTEM_PROMPT = """\
You are a job application email classifier. Extract structured information from job-related emails.

Rules:
1. Return ONLY a single JSON object — no markdown, no explanation, no code fences.
2. If the email is NOT related to a job application return:
   {"company": "", "role": "", "status": "Other", "is_job_email": false}
3. Use exactly one of these status values:
   Applied | Under Review | Interview | Offered | Rejected | Other

4. Status = "Applied" ONLY when there is explicit confirmation that an application
   was submitted or a resume was sent. Exact triggers:
   INBOUND emails (received):
     - "thank you for applying", "we received your application",
       "your application has been received", "application submitted",
       "we have received your resume", "thanks for your interest and application"
   OUTBOUND emails (sent by the user — direction will be marked as "sent"):
     - The user is attaching or mentioning their resume/CV,
       e.g. "please find my resume attached", "I am attaching my CV",
       "here is my resume", "my resume is attached", "PFA my resume/CV"
     - The user is expressing intent to apply for a specific role at a specific company

   DO NOT mark as Applied if:
     - A recruiter is reaching out speculatively ("I think you'd be a great fit")
     - A job alert or newsletter is listing open roles
     - The email is an invitation to apply ("we'd love for you to apply")
     - The email is asking if you are open to opportunities
     - There is no explicit confirmation of submission or resume being sent

5. Other status guidance:
   - Under Review → Email from LinkedIn, ATS, or company saying application is being viewed/considered
     ("your application is under review", "being considered", "application viewed")
   - Interview    → Explicit invitation to call, meeting, or scheduling message
     ("schedule an interview", "please select a time", "interview invitation", "would like to meet")
   - Offered      → Job offer letter or explicit offer
     ("pleased to offer", "offer letter", "we would like to offer you", "congrats on the offer")
   - Rejected     → Rejection, pass, or moving to other candidates
     ("not moving forward", "position has been filled", "other candidates", "unfortunately", "not aligned")
   - Other        → Recruiter outreach, job alerts, newsletters, ambiguous job emails

6. Use "" for company/role if not determinable with confidence.
7. The JSON must have exactly: company, role, status, is_job_email."""

_USER_TEMPLATE = """\
Classify the following email. Return ONLY valid JSON.

Direction: {direction}
Subject: {subject}
From: {sender}
Body:
{body}"""


def _reload_cfg():
    import importlib
    importlib.reload(cfg)


class Classifier:
    """
    Provider-agnostic classifier.

    Pass `overrides` to inject provider/model/api_key/endpoint directly,
    bypassing config.json — used by the test-connection endpoint.
    """

    def __init__(self, overrides: Optional[dict] = None) -> None:
        if overrides:
            self.provider = overrides.get("provider", "ollama").lower()
            self.model    = overrides.get("model", cfg.MODEL_NAME)
            self.api_key  = overrides.get("api_key", "")
            self.endpoint = overrides.get("ollama_endpoint", cfg.OLLAMA_ENDPOINT)
        else:
            _reload_cfg()
            self.provider = cfg.MODEL_PROVIDER.lower()
            self.model    = cfg.MODEL_NAME
            self.api_key  = cfg.API_KEY
            self.endpoint = cfg.OLLAMA_ENDPOINT

        self.timeout = cfg.OLLAMA_TIMEOUT
        logger.info("Classifier: provider=%s model=%s", self.provider, self.model)

    # ── Public ────────────────────────────────────────────────────────────────

    def classify(
        self,
        subject: str,
        sender: str,
        body: str,
        direction: str = "received",   # "received" | "sent"
    ) -> Optional[ClassificationResult]:
        body_trimmed = body[:3_000].strip()
        user_msg = _USER_TEMPLATE.format(
            direction=direction,
            subject=subject,
            sender=sender,
            body=body_trimmed,
        )

        try:
            if self.provider == "ollama":
                raw = self._call_ollama(user_msg)
            elif self.provider == "openai":
                raw = self._call_openai(user_msg)
            elif self.provider == "gemini":
                raw = self._call_gemini(user_msg)
            elif self.provider == "anthropic":
                raw = self._call_anthropic(user_msg)
            else:
                logger.error("Unknown provider: %s", self.provider)
                return None
        except Exception as exc:
            logger.error("Classifier error (%s): %s", self.provider, exc)
            return None

        if raw is None:
            return None

        logger.debug("RAW RESPONSE [%s]: %s", direction, raw)
        return self._parse(raw)

    # ── Test connection ───────────────────────────────────────────────────────

    def test_connection(self) -> tuple[bool, str]:
        probe = _USER_TEMPLATE.format(
            direction="received",
            subject="Test",
            sender="test@example.com",
            body="This is a test.",
        )
        try:
            if self.provider == "ollama":
                raw = self._call_ollama(probe)
            elif self.provider == "openai":
                raw = self._call_openai(probe)
            elif self.provider == "gemini":
                raw = self._call_gemini(probe)
            elif self.provider == "anthropic":
                raw = self._call_anthropic(probe)
            else:
                return False, f"Unknown provider: {self.provider}"
            if raw is None:
                return False, "No response received."
            return True, f"Connected. Model responded ({len(raw)} chars)."
        except Exception as exc:
            return False, str(exc)

    # ── Ollama ────────────────────────────────────────────────────────────────

    def _call_ollama(self, user_msg: str) -> Optional[str]:
        payload = {
            "model": self.model,
            "prompt": f"{_SYSTEM_PROMPT}\n\n{user_msg}",
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_predict": cfg.OLLAMA_NUM_PREDICT,
                "num_ctx":     cfg.OLLAMA_NUM_CTX,
                "num_thread":  cfg.OLLAMA_NUM_THREADS,
            },
        }
        resp = requests.post(self.endpoint, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()

    # ── OpenAI ────────────────────────────────────────────────────────────────

    def _call_openai(self, user_msg: str) -> Optional[str]:
        import openai
        client = openai.OpenAI(api_key=self.api_key)
        resp = client.chat.completions.create(
            model=self.model,
            temperature=0.1,
            max_tokens=256,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
        )
        return resp.choices[0].message.content.strip()

    # ── Gemini ────────────────────────────────────────────────────────────────

    def _call_gemini(self, user_msg: str) -> Optional[str]:
        import google.generativeai as genai
        genai.configure(api_key=self.api_key)
        model = genai.GenerativeModel(
            model_name=self.model,
            system_instruction=_SYSTEM_PROMPT,
        )
        resp = model.generate_content(
            user_msg,
            generation_config=genai.types.GenerationConfig(
                temperature=0.1, max_output_tokens=256
            ),
        )
        return resp.text.strip()

    # ── Anthropic ─────────────────────────────────────────────────────────────

    def _call_anthropic(self, user_msg: str) -> Optional[str]:
        import anthropic
        client = anthropic.Anthropic(api_key=self.api_key)
        msg = client.messages.create(
            model=self.model,
            max_tokens=256,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        return msg.content[0].text.strip()

    # ── Parsing ───────────────────────────────────────────────────────────────

    def _parse(self, raw: str) -> ClassificationResult:
        json_str = self._extract_json(raw)
        if json_str is None:
            logger.warning("Could not extract JSON: %s", raw[:200])
            return ClassificationResult("", "", "Other", raw, False)
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return ClassificationResult("", "", "Other", raw, False)

        company      = str(data.get("company", "")).strip()
        role         = str(data.get("role", "")).strip()
        status       = str(data.get("status", "Other")).strip()
        is_job_email = bool(data.get("is_job_email", True))

        if status not in cfg.VALID_STATUSES:
            status = "Other"

        return ClassificationResult(company, role, status, raw, is_job_email)

    @staticmethod
    def _extract_json(text: str) -> Optional[str]:
        text = text.strip()
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            try:
                json.loads(m.group(1))
                return m.group(1)
            except json.JSONDecodeError:
                pass
        m = re.search(r"\{.*?\}", text, re.DOTALL)
        if m:
            try:
                json.loads(m.group(0))
                return m.group(0)
            except json.JSONDecodeError:
                pass
        return None


OllamaClassifier = Classifier