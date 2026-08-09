"""附件：类型嗅探、路径遏制、落盘去重，以及两条 hydrate 分支。

⚠️ 这些用例**不能碰 .env**：`Settings()` 会读开发机的配置。全部显式构造。
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.attachments import store
from app.attachments.errors import InvalidAttachment, InvalidAttachmentPath
from app.attachments.hydrate import (
    AttachmentHydrator,
    collect_ref_ids,
    placeholder_hydrate,
    ref_block,
)
from app.attachments.image import sniff
from app.attachments.paths import blob_path, normalize_digest, safe_filename
from app.config import Settings
from app.llm.deepseek_provider import to_openai_messages, to_openai_parts
from app.llm.target import DEFAULT_CAPABILITIES, ModelTarget


def png_bytes(width: int = 8, height: int = 4, salt: bytes = b"") -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"tEXt", b"salt\x00" + salt)
        + chunk(b"IEND", b"")
    )


def settings_for(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(
        attachments_path=str(tmp_path),
        anthropic_api_key="",
        deepseek_api_key="",
        **overrides,
    )


def target_with(vision: bool) -> ModelTarget:
    return ModelTarget(
        protocol="openai_compatible",
        model_id="test-model",
        display_name="测试模型",
        capabilities={**DEFAULT_CAPABILITIES, "vision": vision},
    )


# ---------- 类型嗅探：content_type 是用户说了算的，必须自己认 ----------


def test_sniff_reads_dimensions_from_the_header() -> None:
    assert sniff(png_bytes(120, 45)) == ("image/png", 120, 45)


def test_sniff_rejects_bytes_that_are_not_an_image() -> None:
    # 声称是 png 的任意二进制不该被当成图片存下来、更不该 base64 发给模型
    with pytest.raises(InvalidAttachment):
        sniff(b"PK\x03\x04 this is actually a zip" + b"\x00" * 40)


def test_sniff_reads_gif_and_webp() -> None:
    gif = b"GIF89a" + struct.pack("<HH", 7, 9) + b"\x00" * 10
    assert sniff(gif) == ("image/gif", 7, 9)

    webp = (
        b"RIFF" + b"\x00" * 4 + b"WEBPVP8 " + b"\x00" * 4
        + b"\x00\x00\x00" + b"\x9d\x01\x2a" + struct.pack("<HH", 300, 200)
    )
    assert sniff(webp) == ("image/webp", 300, 200)


# ---------- 路径：摘要来自数据库，而数据库不是可信输入 ----------


def test_digest_shape_is_enforced(tmp_path: Path) -> None:
    for bad in ("../../etc/passwd", "", "ZZ" * 32, "abc", 42):
        with pytest.raises(InvalidAttachmentPath):
            normalize_digest(bad)  # type: ignore[arg-type]
    with pytest.raises(InvalidAttachmentPath):
        blob_path(tmp_path, "../../etc/passwd")


def test_blob_path_shards_by_prefix(tmp_path: Path) -> None:
    digest = "a" * 64
    assert blob_path(tmp_path, digest) == (tmp_path / "aa" / digest).resolve()


def test_safe_filename_strips_paths_and_quotes() -> None:
    # 文件名会进 Content-Disposition 和给模型看的文本，两处都不能被截断
    assert safe_filename('../../ev"il\nname.png') == "evilname.png"
    assert safe_filename(None) == "image"


# ---------- 落盘：内容寻址，同一张图只占一份 ----------


async def test_same_bytes_are_stored_once_but_get_their_own_rows(
    session: AsyncSession, tmp_path: Path
) -> None:
    settings = settings_for(tmp_path)
    data = png_bytes()

    first = await store.save_upload(session, settings, data, filename="a.png")
    second = await store.save_upload(session, settings, data, filename="b.png")
    await session.commit()

    assert first.id != second.id
    assert first.sha256 == second.sha256
    assert first.filename == "a.png" and second.filename == "b.png"
    assert len(list(tmp_path.rglob("*.png*"))) == 0
    assert len([p for p in tmp_path.rglob("*") if p.is_file()]) == 1
    assert store.read_blob(settings, first.sha256) == data


async def test_upload_rejects_oversized_files(
    session: AsyncSession, tmp_path: Path
) -> None:
    settings = settings_for(tmp_path, attachment_max_bytes=10)
    with pytest.raises(InvalidAttachment):
        await store.save_upload(session, settings, png_bytes(), filename="a.png")


# ---------- hydrate：分支只看 target 的能力，不看厂商 ----------


async def test_vision_model_gets_a_real_image_block(
    session: AsyncSession, tmp_path: Path
) -> None:
    settings = settings_for(tmp_path)
    row = await store.save_upload(session, settings, png_bytes(), filename="a.png")
    await session.commit()

    messages = [{"role": "user", "content": [ref_block(row), {"type": "text", "text": "这是什么"}]}]
    hydrator = AttachmentHydrator(session, settings, target=target_with(True))
    out = await hydrator.hydrate(messages)

    blocks = out[0]["content"]
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["media_type"] == "image/png"
    assert blocks[0]["source"]["data"]
    assert blocks[1] == {"type": "text", "text": "这是什么"}
    # 落库的那一份不能被改写：存的永远是引用
    assert messages[0]["content"][0]["type"] == "attachment_ref"


async def test_text_model_gets_the_cached_description_with_its_id(
    session: AsyncSession, tmp_path: Path
) -> None:
    settings = settings_for(tmp_path)
    row = await store.save_upload(session, settings, png_bytes(), filename="a.png")
    row.vision_description = "一张写着 ERR_CONN 的截图"
    await session.commit()

    hydrator = AttachmentHydrator(session, settings, target=target_with(False))
    out = await hydrator.hydrate(
        [{"role": "user", "content": [ref_block(row), {"type": "text", "text": "怎么办"}]}]
    )

    text = out[0]["content"][0]["text"]
    assert out[0]["content"][0]["type"] == "text"
    assert "一张写着 ERR_CONN 的截图" in text
    # 编号必须在文本里，否则 image_ask 永远指认不了是哪张图
    assert f"#{row.id}" in text


async def test_text_model_without_a_vision_profile_says_so_instead_of_dropping_the_image(
    session: AsyncSession, tmp_path: Path
) -> None:
    """静默丢图是最坏的结果：模型答非所问，用户根本想不到是图没发出去。"""
    settings = settings_for(tmp_path)
    row = await store.save_upload(session, settings, png_bytes(), filename="a.png")
    await session.commit()

    hydrator = AttachmentHydrator(
        session, settings, target=target_with(False), vision_target=None
    )
    out = await hydrator.hydrate([{"role": "user", "content": [ref_block(row)]}])

    assert "无法识别" in out[0]["content"][0]["text"]


async def test_missing_row_becomes_an_honest_placeholder(
    session: AsyncSession, tmp_path: Path
) -> None:
    settings = settings_for(tmp_path)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "attachment_ref", "id": 999, "kind": "image", "filename": "x.png"}
            ],
        }
    ]
    hydrator = AttachmentHydrator(session, settings, target=target_with(True))
    out = await hydrator.hydrate(messages)
    assert "已不可用" in out[0]["content"][0]["text"]


def test_placeholder_hydrate_never_leaks_raw_refs() -> None:
    """没装配 hydrator 的链路也不能把内部 JSON 当用户的话发出去。"""
    out = placeholder_hydrate(
        [{"role": "user", "content": [{"type": "attachment_ref", "id": 1, "kind": "image", "filename": "a.png"}]}]
    )
    assert out[0]["content"][0] == {"type": "text", "text": "[图片 a.png]"}


def test_collect_ref_ids_dedupes_in_order() -> None:
    messages = [
        {"role": "user", "content": [{"type": "attachment_ref", "id": 3, "kind": "image"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "好"}]},
        {"role": "user", "content": [
            {"type": "attachment_ref", "id": 1, "kind": "image"},
            {"type": "attachment_ref", "id": 3, "kind": "image"},
        ]},
    ]
    assert collect_ref_ids(messages) == [3, 1]


# ---------- OpenAI 兼容协议的翻译 ----------


def test_openai_user_message_with_an_image_becomes_a_multimodal_array() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}},
                {"type": "text", "text": "这是什么"},
            ],
        }
    ]
    out = to_openai_messages(messages)
    # 顺序原样保留：图在前、问题在后，和人贴图的顺序一致（也是两家都推荐的顺序）
    assert out[0]["content"] == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
        {"type": "text", "text": "这是什么"},
    ]


def test_openai_plain_text_message_keeps_the_string_shape() -> None:
    """没有图时不要平白换一种写法 —— 有些兼容服务对数组更挑剔。"""
    out = to_openai_messages([{"role": "user", "content": [{"type": "text", "text": "你好"}]}])
    assert out[0]["content"] == "你好"


def test_openai_parts_skip_blocks_that_have_no_place_in_the_array() -> None:
    parts = to_openai_parts(
        [
            {"type": "thinking", "thinking": "内部推理"},
            {"type": "text", "text": "问题"},
            {"type": "image", "source": {"type": "url", "url": "https://example.com/a.png"}},
        ]
    )
    # thinking 在 OpenAI 协议里有自己的位置；非 base64 的图我们发不出去
    assert parts == [{"type": "text", "text": "问题"}]


async def test_catalog_profile_keeps_the_users_thinking_preference(
    session: AsyncSession, tmp_path: Path
) -> None:
    """内置档案没写 thinking 偏好时，跟随全局默认而不是模型能力。

    拿能力当偏好用的症状是：设置页里关掉思考、保存成功，下一轮又变回开着。
    """
    from app.llm.catalog import resolve_model_target

    settings = Settings(
        attachments_path=str(tmp_path),
        anthropic_api_key="",
        provider="deepseek",
        deepseek_api_key="k",
        deepseek_thinking=False,
    )
    target = await resolve_model_target(session, settings)

    assert target.capabilities["thinking"] is True  # DeepSeek 会思考
    assert target.thinking_default is False  # 但用户不要它思考
