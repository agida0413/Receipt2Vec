"""
테스트는 로컬 .env 와 무관하게 동작해야 한다 (app 모듈 import 전에 실행됨).
load_dotenv 는 이미 설정된 환경변수를 덮어쓰지 않으므로 여기서 고정한 값이 우선한다.
"""
import os

os.environ["LLM_MODE"] = "fallback"
os.environ["GEMINI_API_KEY"] = ""  # 실제 Gemini 호출 방지 (테스트는 가짜 LLM 주입)
