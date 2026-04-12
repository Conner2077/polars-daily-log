import pytest
from auto_daily_log.summarizer.engine import get_llm_engine, LLMEngine
from auto_daily_log.config import LLMConfig, LLMProviderConfig

def test_get_kimi_engine():
    config = LLMConfig(engine="kimi", kimi=LLMProviderConfig(
        api_key="test-key", model="moonshot-v1-8k", base_url="https://api.moonshot.cn/v1"
    ))
    engine = get_llm_engine(config)
    assert isinstance(engine, LLMEngine)
    assert engine.name == "kimi"

def test_get_openai_engine():
    config = LLMConfig(engine="openai", openai=LLMProviderConfig(
        api_key="test-key", model="gpt-4o", base_url="https://api.openai.com/v1"
    ))
    engine = get_llm_engine(config)
    assert engine.name == "openai"

def test_get_ollama_engine():
    config = LLMConfig(engine="ollama", ollama=LLMProviderConfig(
        model="llama3", base_url="http://localhost:11434"
    ))
    engine = get_llm_engine(config)
    assert engine.name == "ollama"

def test_get_claude_engine():
    config = LLMConfig(engine="claude", claude=LLMProviderConfig(
        api_key="test-key", model="claude-sonnet-4-20250514"
    ))
    engine = get_llm_engine(config)
    assert engine.name == "claude"

def test_unknown_engine_raises():
    config = LLMConfig(engine="unknown")
    with pytest.raises(ValueError, match="Unknown LLM engine"):
        get_llm_engine(config)
