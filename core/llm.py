"""Thin wrapper over the OpenAI SDK. One place to change model / handle quirks."""
import json
import os

from dotenv import load_dotenv
from openai import OpenAI, BadRequestError

load_dotenv()

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1")


def _model():
    return os.getenv("OPENAI_MODEL") or DEFAULT_MODEL


def get_client() -> OpenAI:
    return OpenAI()  # reads OPENAI_API_KEY from env / .env


def has_key() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def _create(**kwargs):
    """Some newer models reject temperature=0; retry without it."""
    client = get_client()
    try:
        return client.chat.completions.create(temperature=0, **kwargs)
    except BadRequestError as e:
        if "temperature" in str(e).lower():
            return client.chat.completions.create(**kwargs)
        raise


def chat_json(messages, model: str | None = None) -> dict:
    resp = _create(model=model or DEFAULT_MODEL, messages=messages,
                   response_format={"type": "json_object"})
    return json.loads(resp.choices[0].message.content)


def chat_tools(messages, tools, model: str | None = None):
    return _create(model=model or DEFAULT_MODEL, messages=messages, tools=tools)


def chat_text(messages, model: str | None = None) -> str:
    resp = _create(model=model or DEFAULT_MODEL, messages=messages)
    return resp.choices[0].message.content
