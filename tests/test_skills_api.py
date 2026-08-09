"""技能接口的端到端测试。跑在内存 SQLite 上，不碰网络。"""

import io
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.session import get_session
from app.main import create_app
from app.skills import router as skills_router
from tests.test_skills_store import write_skill


@pytest.fixture
async def client(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    # 显式给出技能目录，不让测试依赖开发机的 .env（CI 里没有那个文件）
    settings = Settings(skills_path=str(tmp_path))

    async def fake_resolve(_session, base=None):
        return settings

    monkeypatch.setattr(skills_router, "resolve_settings", fake_resolve)

    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_lists_installed_skills(client: AsyncClient, tmp_path: Path) -> None:
    write_skill(tmp_path, "pdf", description="处理 PDF 时使用。")

    body = (await client.get("/api/skills")).json()

    assert body["total"] == 1
    assert body["active"] == 1
    assert body["skills"][0]["name"] == "pdf"
    assert body["skills"][0]["enabled"] is True
    # 手动拷进来的技能没有数据库行，来源标成本地而不是空白
    assert body["skills"][0]["source"] == "本地"


async def test_disable_then_enable(client: AsyncClient, tmp_path: Path) -> None:
    write_skill(tmp_path, "pdf")

    disabled = await client.patch("/api/skills/pdf", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert (await client.get("/api/skills")).json()["active"] == 0

    await client.patch("/api/skills/pdf", json={"enabled": True})
    assert (await client.get("/api/skills")).json()["active"] == 1


async def test_detail_returns_body(client: AsyncClient, tmp_path: Path) -> None:
    write_skill(tmp_path, "pdf", body="第一步：先看有没有文本层。")

    body = (await client.get("/api/skills/pdf")).json()

    assert "第一步" in body["body"]


async def test_delete_removes_directory(client: AsyncClient, tmp_path: Path) -> None:
    write_skill(tmp_path, "pdf")

    assert (await client.delete("/api/skills/pdf")).status_code == 204
    assert not (tmp_path / "pdf").exists()
    assert (await client.get("/api/skills")).json()["total"] == 0


async def test_unknown_skill_is_404(client: AsyncClient) -> None:
    assert (await client.get("/api/skills/ghost")).status_code == 404


async def test_bad_name_is_400_not_500(client: AsyncClient) -> None:
    """路径穿越要落到 400，而不是让 realpath 的异常冒成 500。"""
    response = await client.get("/api/skills/..%2F..%2Fetc")
    assert response.status_code in (400, 404)


async def test_upload_installs_from_zip(client: AsyncClient, tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "pdf/SKILL.md", "---\nname: pdf\ndescription: 处理 PDF。\n---\n\n正文。\n"
        )

    response = await client.post(
        "/api/skills/upload",
        files={"file": ("pack.zip", buffer.getvalue(), "application/zip")},
    )

    assert response.status_code == 200
    assert response.json()["installed"][0]["name"] == "pdf"
    assert (tmp_path / "pdf" / "SKILL.md").is_file()


async def test_install_error_carries_the_reason(client: AsyncClient) -> None:
    """400 的消息要能直接显示给人看，别退化成 Internal Server Error。"""
    response = await client.post("/api/skills/install", json={"source": "这是什么"})

    assert response.status_code == 400
    assert "看不懂" in response.json()["detail"]
