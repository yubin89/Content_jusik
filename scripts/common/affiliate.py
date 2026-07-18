"""제휴(쿠팡 파트너스) 링크 블록 + 법정 고지문구.

- 공정위 '추천·보증 등에 관한 표시·광고 심사지침' + 쿠팡 파트너스 약관상
  경제적 대가(수수료)를 받는 게시물에는 고지문구가 '필수'다.
- markdown(자체 사이트)용, Notion 블록(네이버 수동 복붙)용 두 형태로 제공.
"""
from . import notion_client as nc

# 쿠팡 파트너스 공식 권장 고지문구(그대로 사용). 글 안에 잘 보이게 배치할 것.
DISCLOSURE = (
    "이 포스팅은 쿠팡 파트너스 활동의 일환으로, "
    "이에 따른 일정액의 수수료를 제공받습니다."
)


def markdown_block(product):
    """resolve_product() 결과 dict → 글 하단에 붙일 markdown 문자열.

    링크가 없으면(파트너스 미승인/키 미설정) 고지문구 없이 '추천 상품' 자리표시만
    남긴다(수수료를 안 받으므로 고지 불필요, 나중에 링크만 채우면 됨).
    """
    link = product.get("link")
    name = product.get("name") or "추천 상품"
    price = product.get("price")

    if not link:
        return (
            "\n---\n\n"
            "> 💡 관련 상품 링크는 쿠팡 파트너스 승인 후 이 자리에 자동으로 들어갑니다.\n"
        )

    price_str = f" (약 {price:,}원)" if isinstance(price, int) else ""
    return (
        "\n---\n\n"
        "### 🛒 오늘 소개한 제품\n\n"
        f"👉 [{name}{price_str} 쿠팡에서 보기]({link})\n\n"
        f"> {DISCLOSURE}\n"
    )


def notion_blocks(product):
    """resolve_product() 결과 dict → Notion 본문 블록 리스트(네이버 수동 발행용).

    네이버 에디터엔 이 텍스트를 복붙하고, 링크는 네이버 링크 첨부 기능으로 걸면 된다.
    """
    link = product.get("link")
    name = product.get("name") or "추천 상품"
    blocks = [nc.heading("🛒 오늘 소개한 제품", 3)]
    if link:
        blocks.append(nc.paragraph(f"{name}\n{link}"))
        blocks.append(nc.paragraph(f"⚠️ 필수 고지문구(반드시 게시글에 포함): {DISCLOSURE}"))
    else:
        blocks.append(
            nc.paragraph("쿠팡 파트너스 승인 후 상품 링크와 고지문구를 여기에 넣으세요.")
        )
    return blocks
