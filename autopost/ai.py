"""Claude로 사진을 보고 캡션·해시태그를 쓰고, 댓글 답글을 제안한다."""
import base64
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field

from . import config

MAX_IMAGES_TO_AI = 4      # 캐러셀이 길어도 앞 4장만 보여줌 (비용 절약)
IG_CAPTION_LIMIT = 2200   # 인스타 캡션 최대 글자 수
IG_HASHTAG_LIMIT = 30     # 인스타 해시태그 최대 개수


class AIError(Exception):
    pass


class PostDraft(BaseModel):
    caption: str = Field(description="해시태그를 뺀 게시물 본문")
    hashtags: list[str] = Field(description="'#'으로 시작하는 해시태그 목록")


class ReplyDraft(BaseModel):
    reply: str = Field(description="댓글에 달 답글 한 개")


_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # ANTHROPIC_API_KEY 등 환경에서 인증 정보를 찾음
    return _client


def _brand_block(brand: dict) -> str:
    return (
        f"- 가게 이름: {brand['business_name']}\n"
        f"- 업종: {brand['industry']}\n"
        f"- 위치: {brand['location']}\n"
        f"- 말투: {brand['tone']}\n"
        f"- 이모지: {brand['emoji']}\n"
        f"- 안내 문구(필요할 때 끝에 짧게): {brand['call_to_action']}\n"
        f"- 피할 것: {brand['avoid']}"
    )


def _call(system: str, content: list[dict], schema: type[BaseModel]) -> BaseModel:
    try:
        response = _get_client().beta.messages.parse(
            model=config.CLAUDE_MODEL,
            max_tokens=16000,
            output_config={"effort": "medium"},
            # 안전 분류기가 드물게 거절하면 서버가 다른 모델로 자동 재시도
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=system,
            messages=[{"role": "user", "content": content}],
            output_format=schema,
        )
    except anthropic.AuthenticationError as e:
        raise AIError("Claude API 키가 없거나 올바르지 않아요. .env의 ANTHROPIC_API_KEY를 확인하세요.") from e
    except anthropic.PermissionDeniedError as e:
        raise AIError("이 API 키로는 해당 모델을 쓸 수 없어요.") from e
    except anthropic.RateLimitError as e:
        raise AIError("Claude 요청이 너무 많아요. 잠시 후 다시 시도하세요.") from e
    except anthropic.APIStatusError as e:
        raise AIError(f"Claude API 오류 ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise AIError("Claude 서버에 연결하지 못했어요. 인터넷 연결을 확인하세요.") from e
    except (anthropic.AnthropicError, TypeError) as e:
        if "authentication" in str(e).lower():  # 인증 정보를 아예 못 찾은 경우
            raise AIError("Claude API 키가 설정되지 않았어요. .env에 ANTHROPIC_API_KEY를 넣고 앱을 다시 실행하세요.") from e
        raise AIError(f"Claude를 호출하지 못했어요: {e}") from e

    if response.stop_reason == "refusal":
        raise AIError("AI가 이 내용으로는 글을 쓰지 않았어요. 메모를 바꿔서 다시 시도하세요.")
    if response.parsed_output is None:
        raise AIError("AI 응답을 해석하지 못했어요. 다시 시도하세요.")
    return response.parsed_output


def _image_block(path: Path) -> dict:
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def normalize_hashtags(tags: list[str], brand: dict) -> list[str]:
    """고정 해시태그를 앞에 두고, '#' 붙이기·공백 제거·중복 제거·개수 제한."""
    limit = min(int(brand.get("max_hashtags", 15)), IG_HASHTAG_LIMIT)
    result, seen = [], set()
    for tag in [*brand.get("fixed_hashtags", []), *tags]:
        tag = "#" + tag.strip().lstrip("#").replace(" ", "")
        if len(tag) > 1 and tag.lower() not in seen:
            seen.add(tag.lower())
            result.append(tag)
    return result[:limit]


def generate_post(image_paths: list[Path], memo: str, brand: dict,
                  instruction: str = "", previous_caption: str = "") -> tuple[str, list[str]]:
    system = (
        "당신은 동네 작은 가게의 인스타그램 게시물을 대신 써 주는 카피라이터입니다. "
        "사장님이 올린 사진과 짧은 메모를 보고, 사장님이 직접 쓴 것처럼 자연스러운 게시물을 씁니다.\n\n"
        "가게 정보:\n" + _brand_block(brand) + "\n\n"
        "작성 원칙:\n"
        "- 사진에 실제로 보이는 것과 메모에 있는 사실만 씁니다. 메뉴 이름, 가격, 이벤트를 지어내지 않습니다.\n"
        "- 첫 줄은 피드에서 '더 보기' 전에 보이는 부분이라 눈길을 끄는 한 문장으로 씁니다.\n"
        "- 본문은 3~6줄, 짧은 문단으로 나눕니다. 광고 문구보다 일상 이야기처럼.\n"
        f"- 해시태그는 본문에 넣지 말고 hashtags에 따로, {brand.get('max_hashtags', 15)}개 이하로. "
        "지역+업종 태그, 메뉴 태그, 분위기 태그를 섞고, 너무 일반적인 태그(#instagood 등)는 피합니다."
    )
    content = [_image_block(p) for p in image_paths[:MAX_IMAGES_TO_AI]]
    text = f"사장님 메모: {memo.strip() or '(메모 없음 — 사진만 보고 써 주세요)'}"
    if previous_caption and instruction:
        text += f"\n\n이전에 쓴 본문:\n{previous_caption}\n\n수정 요청: {instruction}"
    elif instruction:
        text += f"\n\n추가 요청: {instruction}"
    content.append({"type": "text", "text": text})

    draft: PostDraft = _call(system, content, PostDraft)
    return draft.caption.strip(), normalize_hashtags(draft.hashtags, brand)


def suggest_reply(comment_text: str, commenter: str, post_caption: str, brand: dict) -> str:
    system = (
        "당신은 동네 작은 가게 인스타그램 계정의 댓글 답글을 대신 써 주는 담당자입니다.\n\n"
        "가게 정보:\n" + _brand_block(brand) + "\n\n"
        "답글 원칙:\n"
        "- 1~2문장으로 짧고 따뜻하게. 사장님이 직접 다는 느낌으로.\n"
        "- 질문이면 가게 정보에 있는 사실로만 답하고, 모르는 내용(주차, 예약 등)은 "
        "지어내지 말고 DM이나 전화로 문의해 달라고 안내합니다.\n"
        "- 악성·광고 댓글이면 reply에 '(답글 생략 권장)'이라고만 씁니다."
    )
    text = (
        f"게시물 본문:\n{(post_caption or '')[:600]}\n\n"
        f"@{commenter} 님의 댓글: {comment_text}"
    )
    draft: ReplyDraft = _call(system, [{"type": "text", "text": text}], ReplyDraft)
    return draft.reply.strip()


def compose_caption(caption: str, hashtags: str) -> str:
    """본문 + 해시태그를 인스타 캡션 한 덩어리로 합친다."""
    full = caption.strip()
    if hashtags.strip():
        full += "\n\n" + hashtags.strip()
    return full[:IG_CAPTION_LIMIT]
