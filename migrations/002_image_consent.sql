-- 影像授权核对领域：作品、场次、人物、识别声明、监护、授权事件、
-- 版本派生、用途方案、批准链、发布证据与撤回处置。
-- 所有业务时间均为带偏移量 ISO 8601 字符串，比较在应用层完成。

CREATE TABLE IF NOT EXISTS works (
    work_ref       TEXT PRIMARY KEY,
    author_ref     TEXT NOT NULL,
    source_digest  TEXT NOT NULL,
    title          TEXT,
    recorded_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_ref  TEXT PRIMARY KEY,
    work_ref     TEXT NOT NULL REFERENCES works(work_ref),
    shot_at      TEXT NOT NULL,
    locality     TEXT,
    recorded_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS persons (
    person_ref  TEXT PRIMARY KEY,
    is_minor    INTEGER NOT NULL,
    label       TEXT,
    recorded_at TEXT NOT NULL
);

-- 人物识别声明：某人出现在某原始作品中，bbox 为归一化坐标 [x0,y0,x1,y1]
CREATE TABLE IF NOT EXISTS person_claims (
    claim_id    TEXT PRIMARY KEY,
    work_ref    TEXT NOT NULL REFERENCES works(work_ref),
    person_ref  TEXT NOT NULL REFERENCES persons(person_ref),
    bbox        TEXT,
    role        TEXT NOT NULL,
    declared_by TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE(work_ref, person_ref)
);

CREATE TABLE IF NOT EXISTS guardianships (
    guardianship_id  TEXT PRIMARY KEY,
    child_ref        TEXT NOT NULL REFERENCES persons(person_ref),
    guardian_ref     TEXT NOT NULL REFERENCES persons(person_ref),
    relation         TEXT NOT NULL,
    evidence_digest  TEXT NOT NULL,
    valid_from       TEXT NOT NULL,
    valid_to         TEXT,
    recorded_at      TEXT NOT NULL
);

-- 授权事件（不可变；撤回另立 grant_revocations，不删除本行）
-- purposes/channels/regions 为 JSON 数组；空 channels/regions 分别表示全渠道/全地域
CREATE TABLE IF NOT EXISTS grants (
    grant_id    TEXT PRIMARY KEY,
    person_ref  TEXT NOT NULL REFERENCES persons(person_ref),
    session_ref TEXT REFERENCES sessions(session_ref),
    purposes    TEXT NOT NULL,
    channels    TEXT NOT NULL,
    regions     TEXT NOT NULL,
    valid_from  TEXT NOT NULL,
    valid_until TEXT,
    granted_by  TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS grant_revocations (
    revocation_id TEXT PRIMARY KEY,
    grant_id      TEXT NOT NULL REFERENCES grants(grant_id),
    revoked_at    TEXT NOT NULL,
    reason        TEXT,
    recorded_at   TEXT NOT NULL
);

-- 影像版本：original/crop/mask/composite/subtitle
CREATE TABLE IF NOT EXISTS versions (
    version_id        TEXT PRIMARY KEY,
    work_ref          TEXT NOT NULL REFERENCES works(work_ref),
    parent_version_id TEXT REFERENCES versions(version_id),
    kind              TEXT NOT NULL CHECK(kind IN ('original','crop','mask','composite','subtitle')),
    spec              TEXT NOT NULL,
    digest            TEXT NOT NULL,
    created_by        TEXT NOT NULL,
    created_at        TEXT NOT NULL
);

-- 合成版本的多个来源版本
CREATE TABLE IF NOT EXISTS version_sources (
    version_id        TEXT NOT NULL REFERENCES versions(version_id),
    source_version_id TEXT NOT NULL REFERENCES versions(version_id),
    placement         TEXT,
    PRIMARY KEY (version_id, source_version_id)
);

-- 每个版本上的人物在场状态：present 是否入镜，masked 是否被遮蔽，bbox 为当前画面归一化坐标
CREATE TABLE IF NOT EXISTS version_presence (
    version_id TEXT NOT NULL REFERENCES versions(version_id),
    person_ref TEXT NOT NULL REFERENCES persons(person_ref),
    present    INTEGER NOT NULL,
    masked     INTEGER NOT NULL,
    role       TEXT NOT NULL,
    bbox       TEXT,
    detail     TEXT NOT NULL,
    PRIMARY KEY (version_id, person_ref)
);

CREATE TABLE IF NOT EXISTS proposals (
    proposal_id  TEXT PRIMARY KEY,
    version_id   TEXT NOT NULL REFERENCES versions(version_id),
    purpose      TEXT NOT NULL,
    channel      TEXT NOT NULL,
    region       TEXT NOT NULL,
    use_from     TEXT NOT NULL,
    use_until    TEXT,
    submitted_by TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK(status IN ('pending','approved','rejected'))
);

-- 方案修订：pending 方案每次编辑留痕，已批准方案不可再改
CREATE TABLE IF NOT EXISTS proposal_revisions (
    revision_id  TEXT PRIMARY KEY,
    proposal_id  TEXT NOT NULL REFERENCES proposals(proposal_id),
    editor_ref   TEXT NOT NULL,
    revised_at   TEXT NOT NULL,
    patch        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id   TEXT PRIMARY KEY,
    proposal_id   TEXT NOT NULL REFERENCES proposals(proposal_id),
    reviewer_ref  TEXT NOT NULL,
    decision      TEXT NOT NULL CHECK(decision IN ('approved','rejected')),
    comment       TEXT,
    decided_at    TEXT NOT NULL,
    conflict_check TEXT NOT NULL,
    gate_snapshot TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS publications (
    publication_id TEXT PRIMARY KEY,
    proposal_id    TEXT NOT NULL REFERENCES proposals(proposal_id),
    version_id     TEXT NOT NULL REFERENCES versions(version_id),
    purpose        TEXT NOT NULL,
    channel        TEXT NOT NULL,
    region         TEXT NOT NULL,
    published_at   TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'active'
                   CHECK(status IN ('active','frozen')),
    evidence       TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    frozen_at      TEXT,
    freeze_reason  TEXT
);

-- 撤回生成的处置清单，仅针对受冻结影响的发布渠道
CREATE TABLE IF NOT EXISTS disposition_items (
    item_id        TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL REFERENCES publications(publication_id),
    channel        TEXT NOT NULL,
    action         TEXT NOT NULL,
    reason         TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'open'
                   CHECK(status IN ('open','done')),
    created_at     TEXT NOT NULL,
    handled_at     TEXT
);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('002_image_consent');
