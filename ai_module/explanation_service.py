import json
import os
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Union

from pydantic import BaseModel


class RulesEngineAnalysis(BaseModel):
	supporting_event_ids: List[Union[str, int]]
	timeline: List[Any]
	outcome: Optional[str] = None

	if hasattr(BaseModel, "model_validate"):
		model_config = {"extra": "allow"}
	else:
		class Config:
			extra = "allow"


def _validate_analysis(analysis: Mapping[str, Any]) -> RulesEngineAnalysis:
	model_validate = getattr(RulesEngineAnalysis, "model_validate", None)
	if model_validate:
		return model_validate(analysis)
	return RulesEngineAnalysis.parse_obj(analysis)


def _dump_analysis(analysis: RulesEngineAnalysis) -> Dict[str, Any]:
	model_dump = getattr(analysis, "model_dump", None)
	if model_dump:
		return model_dump()
	return analysis.dict()


class AIExplanationService:
	FALLBACK_RESPONSE = (
		"The rules-engine finding stands. The provided timeline contains the supporting events."
	)
	_CITATION_PATTERN = re.compile(r"\[([^\[\]]+)\]")
	_FINANCIAL_CLAIMS = {
		"refund": (
			re.compile(r"\brefund(?:ed)?\b", re.IGNORECASE),
			re.compile(
				r"\b(?:refund(?: was| has been)? (?:issued|completed|posted|applied|settled|processed)|"
				r"(?:issued|completed|posted|applied|settled|processed) (?:a )?refund|refunded)\b",
				re.IGNORECASE,
			),
		),
		"credit": (
			re.compile(r"\bcredit(?:ed)?\b", re.IGNORECASE),
			re.compile(
				r"\b(?:credit(?: was| has been)? (?:issued|completed|posted|applied|settled|processed)|"
				r"(?:issued|completed|posted|applied|settled|processed) (?:a )?credit|credited)\b",
				re.IGNORECASE,
			),
		),
		"reversal": (
			re.compile(r"\brevers(?:al|ed)\b", re.IGNORECASE),
			re.compile(
				r"\b(?:reversal(?: was| has been)? (?:completed|posted|settled|processed)|"
				r"(?:completed|posted|settled|processed) (?:a )?reversal|reversed)\b",
				re.IGNORECASE,
			),
		),
	}
	SYSTEM_INSTRUCTIONS = (
		"You explain deterministic rules-engine findings. The outcome is authoritative: do not "
		"change, contradict, or reinterpret it. Use only the "
		"provided timeline and supporting event IDs. Do not invent or cite any event ID absent "
		"from supporting_event_ids. Never claim that a refund, credit, or reversal occurred "
		"unless the timeline explicitly records it. Return fewer than 80 words."
	)
	USER_INSTRUCTIONS = (
		"Explain the finding briefly using only the provided analysis. Cite event IDs in "
		"square brackets when useful. Do not add facts or conclusions absent from the data."
	)

	def __init__(
		self,
		llm: Optional[Callable[[str], str]] = None,
		*,
		api_key: Optional[str] = None,
		model: str = "gpt-4o-mini",
		client: Optional[Any] = None,
	) -> None:
		self._llm = llm
		self._api_key = api_key if api_key is not None else os.getenv("GROQ_API_KEY")
		self._model = model
		self._client = client

	def build_system_prompt(self, analysis: Mapping[str, Any]) -> str:
		validated = _validate_analysis(analysis)
		evidence = _dump_analysis(validated)
		return (
			f"{self.SYSTEM_INSTRUCTIONS}\n\n"
			"Deterministic rules-engine analysis (authoritative data):\n"
			f"{json.dumps(evidence, ensure_ascii=True, default=str)}"
		)

	def build_prompt(self, analysis: Mapping[str, Any]) -> str:
		return (
			f"{self.build_system_prompt(analysis)}\n\n{self.USER_INSTRUCTIONS}"
		)

	def _request_llm(self, analysis: Mapping[str, Any], retry: bool) -> Any:
		if self._llm is not None:
			prompt = self.build_prompt(analysis)
			if retry:
				prompt += (
					"\nYour previous answer did not satisfy the constraints. "
					"Return a corrected explanation that satisfies every constraint."
				)
			return self._llm(prompt)

		if not self._api_key:
			return None

		if self._client is None:
			from openai import OpenAI

			self._client = OpenAI(api_key=self._api_key, base_url="https://api.groq.com/openai/v1")

		user_instructions = self.USER_INSTRUCTIONS
		if retry:
			user_instructions += " Your previous answer failed validation; correct it."
		return self._client.chat.completions.create(
			model=self._model,
			temperature=0,
			messages=[
				{"role": "system", "content": self.build_system_prompt(analysis)},
				{"role": "user", "content": user_instructions},
			],
		)

	@staticmethod
	def _response_text(response: Any) -> Optional[str]:
		if isinstance(response, str):
			return response
		try:
			return response.choices[0].message.content
		except (AttributeError, IndexError, TypeError):
			return None

	def validate_response(
		self, response: str, supporting_event_ids: List[Union[str, int]]
	) -> bool:
		allowed_ids = {str(event_id) for event_id in supporting_event_ids}
		cited_ids = {
			citation.strip()
			for citation in self._CITATION_PATTERN.findall(response)
		}
		return cited_ids.issubset(allowed_ids)

	def _is_safe_response(
		self, response: str, analysis: RulesEngineAnalysis
	) -> bool:
		if len(response.split()) >= 80:
			return False

		if not self.validate_response(response, analysis.supporting_event_ids):
			return False

		timeline_entries = [
			json.dumps(entry, ensure_ascii=True, default=str)
			for entry in analysis.timeline
		]
		for claim_pattern, evidence_pattern in self._FINANCIAL_CLAIMS.values():
			if claim_pattern.search(response) and not any(
				evidence_pattern.search(entry)
				for entry in timeline_entries
			):
				return False

		return True

	def explain(self, analysis: Mapping[str, Any]) -> str:
		try:
			validated = _validate_analysis(analysis)
		except Exception:
			return self.FALLBACK_RESPONSE

		if self._llm is None and not self._api_key and self._client is None:
			return self.FALLBACK_RESPONSE

		for attempt in range(2):
			try:
				response = self._request_llm(analysis, retry=attempt == 1)
			except Exception:
				continue

			text = self._response_text(response)
			if text and self._is_safe_response(text, validated):
				return text.strip()

		return self.FALLBACK_RESPONSE
