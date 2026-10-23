"""Model IDs and per-role call parameters.

IDs are copied from the Token Factory catalog snapshot in evidence/platform/ (a test checks every ID against it):
the catalog spells them three different ways, so they are never typed from memory. Each role can be overridden
with an environment variable, and each Nemotron role has a fallback the client switches to on HTTP 404.
"""
import os

LIGHTNING = os.environ.get("SA_LIGHTNING", "nvidia/Nemotron-3_5-Lightning")
SUPER = os.environ.get("SA_SUPER", "nvidia/nemotron-3-super-120b-a12b")
ULTRA = os.environ.get("SA_ULTRA", "nvidia/Nemotron-3-Ultra-550b-a55b")
SUPER_VL = os.environ.get("SA_SUPER_VL", "nvidia/Nemotron-3.5-Super-VL")

# Buyer-simulator candidates: deliberately not NVIDIA models, so Nemotron never grades itself.
QWEN3_30B = "Qwen/Qwen3-30B-A3B-Instruct-2507"
DEEPSEEK_V4_FLASH = "deepseek-ai/DeepSeek-V4-Flash-0731"
GPT_OSS_120B = "openai/gpt-oss-120b"
BUYER = os.environ.get("SA_BUYER", QWEN3_30B)

NVIDIA = (LIGHTNING, SUPER, ULTRA, SUPER_VL)
FALLBACKS = {LIGHTNING: [SUPER], SUPER: [SUPER_VL], ULTRA: [SUPER], SUPER_VL: [SUPER]}

SAMPLING = {"temperature": 1.0, "top_p": 0.95}  # NVIDIA's recommendation for Lightning, Super, Ultra and Super-VL
REASONING_OFF = {"reasoning_effort": "none"}
THINK_TOOLS = {"chat_template_kwargs": {"enable_thinking": True, "force_nonempty_content": True}}
NONEMPTY = {"chat_template_kwargs": {"force_nonempty_content": True}}
