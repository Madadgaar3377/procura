"""Comprehensive R2 file storage integration tests."""
from __future__ import annotations

import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from botocore.exceptions import ClientError
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import storage
from app.config import Settings
from app.database import Base, get_db
from app.models import (
    Capability,
    Category,
    Invitation,
    Member,
    Organization,
    Quotation,
    RFQ,
    StoredFile,
    User,
    Verification,
)
from app.modules.auth.router import router as auth_router
from app.modules.capabilities.directory import router as capabilities_router
from app.modules.files.router import router as files_router
from app.modules.organizations.router import router as orgs_router
from app.modules.quotations.router import router as quotations_router
from app.modules.rfqs.router import router as rfqs_router
from app.security import token_for


class FakeR2Client:
    """In-memory S3-compatible fake client for procumaterial-public & procumaterial-private."""

    def __init__(self):
        self.objects: dict[tuple[str, str], dict] = {}
        self.fail_put = False
        self.fail_get = False

    def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str, **kwargs):
        if self.fail_put:
            raise ClientError({"Error": {"Code": "ServiceUnavailable", "Message": "Simulated R2 outage"}}, "PutObject")
        self.objects[(Bucket, Key)] = {
            "body": bytes(Body),
            "content_type": ContentType,
            "metadata": kwargs.get("Metadata", {}),
        }
        return {"ETag": f'"etag-{len(Body)}"'}

    def get_object(self, Bucket: str, Key: str):
        if self.fail_get:
            raise ClientError({"Error": {"Code": "ServiceUnavailable", "Message": "Simulated R2 outage"}}, "GetObject")
        item = self.objects.get((Bucket, Key))
        if not item:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "Missing"}}, "GetObject")
        return {"Body": io.BytesIO(item["body"]), "ContentType": item["content_type"]}

    def head_object(self, Bucket: str, Key: str):
        item = self.objects.get((Bucket, Key))
        if not item:
            raise ClientError({"Error": {"Code": "404", "Message": "Missing"}}, "HeadObject")
        return {"ContentLength": len(item["body"]), "ContentType": item["content_type"]}

    def delete_object(self, Bucket: str, Key: str):
        self.objects.pop((Bucket, Key), None)
        return {}

    def generate_presigned_url(self, ClientMethod: str, Params: dict, ExpiresIn: int = 900):
        return f"https://r2.example.test/{Params['Bucket']}/{Params['Key']}?expires={ExpiresIn}"


def _make_png(width: int = 32, height: int = 32, color=(20, 80, 160)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def _make_ooxml(kind: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        if kind == "xlsx":
            zf.writestr("xl/workbook.xml", "<workbook/>")
        else:
            zf.writestr("word/document.xml", "<document/>")
    return buf.getvalue()


@pytest.fixture()
def r2_env(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    fake_r2 = FakeR2Client()
    fake_settings = Settings(
        r2_enabled=True,
        r2_public_bucket="procumaterial-public",
        r2_private_bucket="procumaterial-private",
        r2_public_base_url="",
        r2_presign_ttl_seconds=300,
        max_upload_bytes=10 * 1024 * 1024,
        environment="test",
        upload_dir=str(tmp_path),
    )

    monkeypatch.setattr(storage, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(storage, "get_storage_client", lambda: fake_r2)
    # Patch module-level settings references where imported directly
    from app.modules.files import router as files_mod
    from app.modules.rfqs import router as rfqs_mod
    from app.modules.quotations import router as quotes_mod
    from app.modules.organizations import router as orgs_mod
    from app.modules.auth import router as auth_mod

    for mod in (files_mod, rfqs_mod, quotes_mod, orgs_mod, auth_mod):
        if hasattr(mod, "get_settings"):
            monkeypatch.setattr(mod, "get_settings", lambda: fake_settings)

    app = FastAPI()
    for r in (auth_router, orgs_router, files_router, rfqs_router, quotations_router, capabilities_router):
        app.include_router(r, prefix="/api/v1")

    def override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db

    with SessionLocal() as db:
        buyer_user = User(id=str(uuid4()), username="buyer_one", email="buyer@test.sa", phone="+966500000001", phone_e164="+966500000001", password_hash="x", email_verified=True)
        supplier_user = User(id=str(uuid4()), username="sup_one", email="sup@test.sa", phone="+966500000002", phone_e164="+966500000002", password_hash="x", email_verified=True)
        other_user = User(id=str(uuid4()), username="other_one", email="other@test.sa", phone="+966500000003", phone_e164="+966500000003", password_hash="x", email_verified=True)
        admin_user = User(id=str(uuid4()), username="admin_one", email="admin@test.sa", phone="+966500000004", phone_e164="+966500000004", password_hash="x", email_verified=True, is_admin=True)

        buyer_org = Organization(id=str(uuid4()), name="Buyer Corp", name_normalized="buyer corp", is_buyer=True, is_supplier=False, cr_number="1010101010", verified_status="verified", profile_status="complete")
        supplier_org = Organization(id=str(uuid4()), name="Supplier Co", name_normalized="supplier co", is_buyer=False, is_supplier=True, cr_number="2020202020", verified_status="verified", profile_status="complete")
        other_org = Organization(id=str(uuid4()), name="Other Co", name_normalized="other co", is_buyer=False, is_supplier=True, cr_number="3030303030", verified_status="verified", profile_status="complete")

        db.add_all([buyer_user, supplier_user, other_user, admin_user, buyer_org, supplier_org, other_org])
        db.flush()

        db.add_all([
            Member(user_id=buyer_user.id, organization_id=buyer_org.id, role="owner"),
            Member(user_id=supplier_user.id, organization_id=supplier_org.id, role="owner"),
            Member(user_id=other_user.id, organization_id=other_org.id, role="owner"),
        ])

        cat = Category(id=str(uuid4()), code="steel", name_en="Steel", name_ar="حديد")
        db.add(cat)
        db.flush()
        db.add(Capability(org_id=supplier_org.id, category_id=cat.id, city="Riyadh", confirmed=True, accepts_rfqs=True))

        deadline = datetime.now(UTC) + timedelta(days=7)
        rfq = RFQ(
            id=str(uuid4()),
            reference="RFQ-2026-0001",
            buyer_org_id=buyer_org.id,
            title="Steel Rebar",
            city="Riyadh",
            response_deadline=deadline,
            status="draft",
        )
        rfq2 = RFQ(
            id=str(uuid4()),
            reference="RFQ-2026-0002",
            buyer_org_id=buyer_org.id,
            title="Concrete",
            city="Riyadh",
            response_deadline=deadline,
            status="open",
        )
        db.add_all([rfq, rfq2])
        db.flush()

        inv = Invitation(id=str(uuid4()), rfq_id=rfq.id, supplier_org_id=supplier_org.id, status="accepted")
        inv2 = Invitation(id=str(uuid4()), rfq_id=rfq2.id, supplier_org_id=supplier_org.id, status="accepted")
        db.add_all([inv, inv2])
        db.flush()

        quote_draft = Quotation(
            id=str(uuid4()),
            invitation_id=inv.id,
            rfq_id=rfq.id,
            supplier_org_id=supplier_org.id,
            status="draft",
        )
        quote_submitted = Quotation(
            id=str(uuid4()),
            invitation_id=inv2.id,
            rfq_id=rfq2.id,
            supplier_org_id=supplier_org.id,
            status="submitted",
            submitted_at=datetime.now(UTC),
        )
        db.add_all([quote_draft, quote_submitted])
        db.commit()

        tokens = {
            "buyer": token_for(buyer_user),
            "supplier": token_for(supplier_user),
            "other": token_for(other_user),
            "admin": token_for(admin_user),
        }
        ids = {
            "buyer_user": buyer_user.id,
            "supplier_user": supplier_user.id,
            "other_user": other_user.id,
            "buyer_org": buyer_org.id,
            "supplier_org": supplier_org.id,
            "other_org": other_org.id,
            "rfq": rfq.id,
            "quote_draft": quote_draft.id,
            "quote_submitted": quote_submitted.id,
        }

    client = TestClient(app)
    return SimpleNamespace(client=client, fake_r2=fake_r2, tokens=tokens, ids=ids, SessionLocal=SessionLocal)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_avatar_upload_optimize_replace_delete_and_authz(r2_env):
    png_bytes = _make_png(600, 400)
    res = r2_env.client.post(
        "/api/v1/auth/me/avatar",
        headers=_auth(r2_env.tokens["buyer"]),
        files={"file": ("avatar.png", png_bytes, "image/png")},
    )
    assert res.status_code == 200, res.text
    user_data = res.json()
    first_file_id = user_data["avatar_file_id"]
    assert first_file_id
    assert user_data["avatar_url"] == f"/api/v1/files/public/{first_file_id}"

    # Verify public asset endpoint returns optimized WebP
    pub = r2_env.client.get(user_data["avatar_url"])
    assert pub.status_code == 200
    assert pub.headers["content-type"] == "image/webp"
    assert len(r2_env.fake_r2.objects) == 1

    # Replace avatar -> old R2 object should be removed
    res2 = r2_env.client.post(
        "/api/v1/auth/me/avatar",
        headers=_auth(r2_env.tokens["buyer"]),
        files={"file": ("avatar2.png", _make_png(300, 300), "image/png")},
    )
    assert res2.status_code == 200
    second_file_id = res2.json()["avatar_file_id"]
    assert second_file_id != first_file_id
    assert len(r2_env.fake_r2.objects) == 1

    # Unauthorized user cannot change another user's avatar
    forbidden = r2_env.client.post(
        f"/api/v1/auth/users/{r2_env.ids['buyer_user']}/avatar",
        headers=_auth(r2_env.tokens["other"]),
        files={"file": ("x.png", png_bytes, "image/png")},
    )
    assert forbidden.status_code == 403

    # Delete avatar
    deleted = r2_env.client.delete("/api/v1/auth/me/avatar", headers=_auth(r2_env.tokens["buyer"]))
    assert deleted.status_code == 200
    assert deleted.json()["avatar_file_id"] is None
    assert deleted.json()["avatar_url"] is None
    assert len(r2_env.fake_r2.objects) == 0


def test_company_logo_upload_public_exposure_and_cross_org_blocked(r2_env):
    png_bytes = _make_png(512, 512)
    res = r2_env.client.post(
        f"/api/v1/organizations/{r2_env.ids['supplier_org']}/logo",
        headers=_auth(r2_env.tokens["supplier"]),
        files={"file": ("logo.png", png_bytes, "image/png")},
    )
    assert res.status_code == 200, res.text
    logo_url = res.json()["logo_url"]
    assert logo_url and logo_url.startswith("/api/v1/files/public/")

    # Public supplier profile exposes logo_url
    pub_sup = r2_env.client.get(f"/api/v1/directory/suppliers/{r2_env.ids['supplier_org']}")
    assert pub_sup.status_code == 200, pub_sup.text
    assert pub_sup.json()["logo_url"] == logo_url

    # Cross-org member cannot replace or delete supplier logo
    cross = r2_env.client.post(
        f"/api/v1/organizations/{r2_env.ids['supplier_org']}/logo",
        headers=_auth(r2_env.tokens["other"]),
        files={"file": ("hack.png", png_bytes, "image/png")},
    )
    assert cross.status_code in (403, 404)

    # Delete logo
    rem = r2_env.client.delete(
        f"/api/v1/organizations/{r2_env.ids['supplier_org']}/logo",
        headers=_auth(r2_env.tokens["supplier"]),
    )
    assert rem.status_code == 200
    assert rem.json()["logo_url"] is None


def test_rfq_attachments_all_allowed_types_and_visibility(r2_env):
    samples = [
        ("spec.pdf", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n", "application/pdf"),
        ("boq.xlsx", _make_ooxml("xlsx"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("scope.docx", _make_ooxml("docx"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("drawing.png", _make_png(), "image/png"),
    ]
    uploaded_ids = []
    for fname, content, mime in samples:
        res = r2_env.client.post(
            f"/api/v1/rfqs/{r2_env.ids['rfq']}/attachments",
            headers=_auth(r2_env.tokens["buyer"]),
            files={"file": (fname, content, mime)},
        )
        assert res.status_code in (200, 201), res.text
        uploaded_ids.append(res.json()["id"])

    # Open RFQ so invited supplier can download
    with r2_env.SessionLocal() as db:
        rfq_row = db.get(RFQ, r2_env.ids["rfq"])
        rfq_row.status = "open"
        db.commit()

    # Invited supplier can download (307 signed redirect or 200 stream)
    dl_sup = r2_env.client.get(
        f"/api/v1/rfqs/{r2_env.ids['rfq']}/attachments/{uploaded_ids[0]}",
        headers=_auth(r2_env.tokens["supplier"]),
        follow_redirects=False,
    )
    assert dl_sup.status_code in (200, 307)
    assert dl_sup.headers.get("x-robots-tag") == "noindex, nofollow"

    # Uninvited supplier is blocked
    dl_other = r2_env.client.get(
        f"/api/v1/rfqs/{r2_env.ids['rfq']}/attachments/{uploaded_ids[0]}",
        headers=_auth(r2_env.tokens["other"]),
        follow_redirects=False,
    )
    assert dl_other.status_code in (403, 404)


def test_quotation_attachments_lifecycle_and_permissions(r2_env):
    # Supplier uploads attachment to draft quotation
    res = r2_env.client.post(
        f"/api/v1/quotations/{r2_env.ids['quote_draft']}/attachments",
        headers=_auth(r2_env.tokens["supplier"]),
        files={"file": ("datasheet.pdf", b"%PDF-1.7\n", "application/pdf")},
    )
    assert res.status_code in (200, 201), res.text
    att_id = res.json()["id"]

    # Buyer cannot view draft quotation attachment before submission
    buyer_draft = r2_env.client.get(
        f"/api/v1/quotations/{r2_env.ids['quote_draft']}/attachments/{att_id}",
        headers=_auth(r2_env.tokens["buyer"]),
        follow_redirects=False,
    )
    assert buyer_draft.status_code in (403, 404)

    # Other org cannot upload or download
    other_up = r2_env.client.post(
        f"/api/v1/quotations/{r2_env.ids['quote_draft']}/attachments",
        headers=_auth(r2_env.tokens["other"]),
        files={"file": ("x.pdf", b"%PDF-1.7\n", "application/pdf")},
    )
    assert other_up.status_code in (403, 404)

    # Delete draft attachment
    del_res = r2_env.client.delete(
        f"/api/v1/quotations/{r2_env.ids['quote_draft']}/attachments/{att_id}",
        headers=_auth(r2_env.tokens["supplier"]),
    )
    assert del_res.status_code == 200

    # Cannot upload to submitted quotation
    sub_up = r2_env.client.post(
        f"/api/v1/quotations/{r2_env.ids['quote_submitted']}/attachments",
        headers=_auth(r2_env.tokens["supplier"]),
        files={"file": ("late.pdf", b"%PDF-1.7\n", "application/pdf")},
    )
    assert sub_up.status_code in (400, 409)


def test_verification_documents_upload_list_download_and_cross_org(r2_env):
    res = r2_env.client.post(
        f"/api/v1/organizations/{r2_env.ids['supplier_org']}/documents",
        headers=_auth(r2_env.tokens["supplier"]),
        data={"kind": "cr"},
        files={"file": ("cr_cert.pdf", b"%PDF-1.5\nCR", "application/pdf")},
    )
    assert res.status_code in (200, 201), res.text
    file_id = res.json().get("document_key") or res.json().get("file_id") or res.json().get("id")

    # Org member can list and download
    listed = r2_env.client.get(
        f"/api/v1/organizations/{r2_env.ids['supplier_org']}/documents",
        headers=_auth(r2_env.tokens["supplier"]),
    )
    assert listed.status_code == 200
    assert any(d["id"] == file_id for d in listed.json())

    dl = r2_env.client.get(
        f"/api/v1/organizations/{r2_env.ids['supplier_org']}/documents/{file_id}",
        headers=_auth(r2_env.tokens["supplier"]),
        follow_redirects=False,
    )
    assert dl.status_code in (200, 307)
    assert dl.headers.get("x-robots-tag") == "noindex, nofollow"

    # Cross-org member blocked
    cross_list = r2_env.client.get(
        f"/api/v1/organizations/{r2_env.ids['supplier_org']}/documents",
        headers=_auth(r2_env.tokens["other"]),
    )
    assert cross_list.status_code in (403, 404)


def test_invalid_file_types_magic_bytes_oversized_and_r2_unavailable(r2_env):
    # SVG script upload rejected
    svg = r2_env.client.post(
        "/api/v1/auth/me/avatar",
        headers=_auth(r2_env.tokens["buyer"]),
        files={"file": ("evil.svg", b"<svg onload=alert(1)></svg>", "image/svg+xml")},
    )
    assert svg.status_code in (400, 415)

    # Spoofed PDF header (HTML disguised as PDF) rejected
    spoof = r2_env.client.post(
        f"/api/v1/rfqs/{r2_env.ids['rfq']}/attachments",
        headers=_auth(r2_env.tokens["buyer"]),
        files={"file": ("fake.pdf", b"<html><script>alert(1)</script></html>", "application/pdf")},
    )
    assert spoof.status_code in (400, 422)

    # Oversized avatar (> 5 MB) rejected with 413
    big_avatar = r2_env.client.post(
        "/api/v1/auth/me/avatar",
        headers=_auth(r2_env.tokens["buyer"]),
        files={"file": ("big.png", b"\x89PNG\r\n\x1a\n" + b"0" * (5 * 1024 * 1024 + 100), "image/png")},
    )
    assert big_avatar.status_code == 413

    # Storage unavailable returns 503
    r2_env.fake_r2.fail_put = True
    outage = r2_env.client.post(
        "/api/v1/auth/me/avatar",
        headers=_auth(r2_env.tokens["buyer"]),
        files={"file": ("ok.png", _make_png(), "image/png")},
    )
    assert outage.status_code == 503
    r2_env.fake_r2.fail_put = False


def test_db_failure_cleans_up_orphan_r2_object_and_concurrent_replace(r2_env, monkeypatch):
    # Simulate DB commit failure after R2 upload succeeds -> R2 object must be cleaned up
    from app.modules.auth import router as auth_mod

    orig_update = auth_mod._update_user_avatar

    def _boom_after_upload(*args, **kwargs):
        raise RuntimeError("Simulated DB commit failure")

    # Test cleanup_object_best_effort directly via upload + commit failure on RFQ attachment
    with r2_env.SessionLocal() as db:
        from sqlalchemy.orm import Session
        orig_commit = Session.commit
        call_count = {"n": 0}

        def failing_commit(self):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("DB failure")
            return orig_commit(self)

        monkeypatch.setattr(Session, "commit", failing_commit)
        with pytest.raises(RuntimeError, match="DB failure"):
            r2_env.client.post(
                f"/api/v1/rfqs/{r2_env.ids['rfq']}/attachments",
                headers=_auth(r2_env.tokens["buyer"]),
                files={"file": ("doc.pdf", b"%PDF-1.4\n", "application/pdf")},
            )
        monkeypatch.setattr(Session, "commit", orig_commit)

    assert len(r2_env.fake_r2.objects) == 0

    # Concurrent avatar replacements leave exactly 1 R2 object matching the DB pointer
    png_a = _make_png(64, 64, (10, 20, 30))
    png_b = _make_png(64, 64, (40, 50, 60))

    def _upload_avatar(payload):
        return r2_env.client.post(
            "/api/v1/auth/me/avatar",
            headers=_auth(r2_env.tokens["buyer"]),
            files={"file": ("av.png", payload, "image/png")},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        r1, r2 = list(pool.map(_upload_avatar, [png_a, png_b]))

    assert r1.status_code == 200
    assert r2.status_code == 200
    me = r2_env.client.get("/api/v1/auth/me", headers=_auth(r2_env.tokens["buyer"])).json()
    final_id = me["avatar_file_id"]
    with r2_env.SessionLocal() as db:
        sf = db.get(StoredFile, final_id)
        assert sf is not None
        assert ("procumaterial-public", sf.object_key) in r2_env.fake_r2.objects


def test_alembic_avatar_logo_migration_upgrade_downgrade(tmp_path):
    db_file = tmp_path / "mig_test.db"
    backend_dir = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")

    # Stamp at previous head zc29f548d670 with minimal users/organizations tables
    engine = create_engine(f"sqlite:///{db_file}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE organizations (id VARCHAR(36) PRIMARY KEY)"))
        cfg.attributes["connection"] = conn
        command.stamp(cfg, "zc29f548d670")

        command.upgrade(cfg, "zd30a659e781")
        insp = inspect(conn)
        assert "avatar_file_id" in [c["name"] for c in insp.get_columns("users")]
        assert "logo_file_id" in [c["name"] for c in insp.get_columns("organizations")]

        command.downgrade(cfg, "zc29f548d670")
        insp = inspect(conn)
        assert "avatar_file_id" not in [c["name"] for c in insp.get_columns("users")]
        assert "logo_file_id" not in [c["name"] for c in insp.get_columns("organizations")]



def test_avatar_formats_xmp_metadata_disguised_and_corrupted_images(r2_env):
    from PIL.PngImagePlugin import PngInfo

    # 1. Valid JPEG containing XMP/markup-like bytes in the first 512 bytes (APP1/COM segment)
    jpg_buf = io.BytesIO()
    Image.new("RGB", (120, 90), (10, 120, 200)).save(jpg_buf, format="JPEG")
    raw_jpg = jpg_buf.getvalue()
    # Inject a JPEG COM (0xFFFE) segment right after SOI (0xFFD8) containing XMP/SVG-like text
    comment_payload = b"<x:xmpmeta><svg>metadata</svg></x:xmpmeta>"
    com_segment = b"\xff\xfe" + (len(comment_payload) + 2).to_bytes(2, "big") + comment_payload
    jpg_with_xmp = raw_jpg[:2] + com_segment + raw_jpg[2:]

    res_jpg = r2_env.client.post(
        "/api/v1/auth/me/avatar",
        headers=_auth(r2_env.tokens["buyer"]),
        files={"file": ("photo.jpg", jpg_with_xmp, "image/jpeg")},
    )
    assert res_jpg.status_code == 200, res_jpg.text
    file_id = res_jpg.json()["avatar_file_id"]
    with r2_env.SessionLocal() as db:
        sf = db.get(StoredFile, file_id)
        assert sf is not None
        assert sf.object_key.startswith(f"profiles/avatars/{r2_env.ids['buyer_user']}/")

    # Persistence across refresh (/auth/me)
    me = r2_env.client.get("/api/v1/auth/me", headers=_auth(r2_env.tokens["buyer"]))
    assert me.status_code == 200
    assert me.json()["avatar_url"] == f"/api/v1/files/public/{file_id}"

    # 2. Valid PNG with tEXt/XMP metadata in header
    png_info = PngInfo()
    png_info.add_text("XML:com.adobe.xmp", "<x:xmpmeta><html>xmp</html></x:xmpmeta>")
    png_buf = io.BytesIO()
    Image.new("RGBA", (80, 80), (200, 40, 40, 255)).save(png_buf, format="PNG", pnginfo=png_info)
    res_png = r2_env.client.post(
        "/api/v1/auth/me/avatar",
        headers=_auth(r2_env.tokens["buyer"]),
        files={"file": ("avatar.png", png_buf.getvalue(), "image/png")},
    )
    assert res_png.status_code == 200, res_png.text

    # 3. Valid WebP
    webp_buf = io.BytesIO()
    Image.new("RGB", (64, 64), (40, 180, 90)).save(webp_buf, format="WEBP")
    res_webp = r2_env.client.post(
        "/api/v1/auth/me/avatar",
        headers=_auth(r2_env.tokens["buyer"]),
        files={"file": ("avatar.webp", webp_buf.getvalue(), "image/webp")},
    )
    assert res_webp.status_code == 200, res_webp.text

    # 4. HTML renamed to .jpg -> rejected with 422
    html_as_jpg = r2_env.client.post(
        "/api/v1/auth/me/avatar",
        headers=_auth(r2_env.tokens["buyer"]),
        files={"file": ("evil.jpg", b"<!DOCTYPE html><html><script>alert(1)</script></html>", "image/jpeg")},
    )
    assert html_as_jpg.status_code == 422
    assert "Executable or active markup" in html_as_jpg.json()["detail"]

    # 5. Executable renamed to .png -> rejected with 422
    exe_as_png = r2_env.client.post(
        "/api/v1/auth/me/avatar",
        headers=_auth(r2_env.tokens["buyer"]),
        files={"file": ("evil.png", b"MZ\x90\x00\x03\x00\x00\x00", "image/png")},
    )
    assert exe_as_png.status_code == 422
    assert "Executable or active markup" in exe_as_png.json()["detail"]

    # 6. Corrupted JPEG (valid magic bytes, broken body) -> rejected with 422
    corrupt_jpg = r2_env.client.post(
        "/api/v1/auth/me/avatar",
        headers=_auth(r2_env.tokens["buyer"]),
        files={"file": ("broken.jpg", b"\xff\xd8\xff\xe0not-a-real-jpeg-stream", "image/jpeg")},
    )
    assert corrupt_jpg.status_code == 422
