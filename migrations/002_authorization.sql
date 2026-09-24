-- 影像授权核对：原始作品、识别声明、监护关系、拍摄场次、授权、
-- 可追溯影像版本、用途方案、审批链、发布证据、渠道冻结与处置清单。

-- 拍摄场次
CREATE TABLE IF NOT EXISTS sessions (
    session_ref  TEXT PRIMARY KEY,
    shot_at      TEXT NOT NULL,           -- ISO 8601 with offset
    location_ref TEXT,
    created_at   TEXT NOT NULL
);

-- 原始作品（摄影作品声明）
CREATE TABLE IF NOT EXISTS works (
    work_ref     TEXT PRIMARY KEY,
    session_ref  TEXT NOT NULL REFERENCES sessions(session_ref),
    author_ref   TEXT NOT NULL,           -- 作者/著作权方引用编号
    digest       TEXT NOT NULL,           -- sha256:<hex>
    captured_at  TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

-- 人物：观众、临时演员、未成年人等；监护关系为自引用
CREATE TABLE IF NOT EXISTS persons (
    person_ref   TEXT PRIMARY KEY,
    kind         TEXT NOT NULL DEFAULT 'visitor',  -- visitor/extra/staff/...
    is_minor     INTEGER NOT NULL DEFAULT 0,
    guardian_ref TEXT REFERENCES persons(person_ref),
    created_at   TEXT NOT NULL
);

-- 人物识别声明：某人出现在某原始作品的哪个区域（归一化坐标 x/y/w/h）
CREATE TABLE IF NOT EXISTS recognitions (
    recognition_ref TEXT PRIMARY KEY,
    work_ref        TEXT NOT NULL REFERENCES works(work_ref),
    person_ref      TEXT NOT NULL REFERENCES persons(person_ref),
    region_json     TEXT NOT NULL DEFAULT '{}',
    declared_by     TEXT NOT NULL,
    declared_at     TEXT NOT NULL
);

-- 授权（许可）：用途、地域、期限；未成年人由监护人授予
CREATE TABLE IF NOT EXISTS grants (
    grant_ref        TEXT PRIMARY KEY,
    person_ref       TEXT NOT NULL REFERENCES persons(person_ref),
    granted_by_ref   TEXT,                       -- 授权人；未成年人须为监护人
    purposes_json    TEXT NOT NULL DEFAULT '[]', -- 允许的用途
    territories_json TEXT NOT NULL DEFAULT '[]', -- 允许的地域，ALL 表示不限
    valid_from       TEXT NOT NULL,
    valid_until      TEXT,                       -- NULL 表示长期有效
    created_at       TEXT NOT NULL               -- 授权登记时间，迟到授权不得倒改历史
);

-- 授权撤回
CREATE TABLE IF NOT EXISTS withdrawals (
    withdrawal_ref TEXT PRIMARY KEY,
    grant_ref      TEXT NOT NULL REFERENCES grants(grant_ref),
    withdrawn_at   TEXT NOT NULL,
    reason         TEXT,
    created_at     TEXT NOT NULL
);

-- 可追溯影像版本：原始/裁剪/打码/合成/字幕替换
CREATE TABLE IF NOT EXISTS versions (
    version_ref        TEXT PRIMARY KEY,
    work_ref           TEXT NOT NULL REFERENCES works(work_ref),
    parent_version_ref TEXT REFERENCES versions(version_ref),
    kind               TEXT NOT NULL CHECK (
        kind IN ('original', 'crop', 'mask', 'composite', 'subtitle')
    ),
    params_json        TEXT NOT NULL DEFAULT '{}',
    digest             TEXT NOT NULL,
    created_at         TEXT NOT NULL
);

-- 合成版本的多个来源版本
CREATE TABLE IF NOT EXISTS version_sources (
    version_ref        TEXT NOT NULL REFERENCES versions(version_ref),
    source_version_ref TEXT NOT NULL REFERENCES versions(version_ref),
    PRIMARY KEY (version_ref, source_version_ref)
);

-- 版本中的人物状态：visible（可识别）/ masked（已遮蔽，不再需要授权）
CREATE TABLE IF NOT EXISTS version_persons (
    version_ref TEXT NOT NULL REFERENCES versions(version_ref),
    person_ref  TEXT NOT NULL REFERENCES persons(person_ref),
    state       TEXT NOT NULL CHECK (state IN ('visible', 'masked')),
    region_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (version_ref, person_ref)
);

-- 用途方案：编辑提交，等待无利益关系审核人确认
CREATE TABLE IF NOT EXISTS plans (
    plan_ref              TEXT PRIMARY KEY,
    version_ref           TEXT NOT NULL REFERENCES versions(version_ref),
    purpose               TEXT NOT NULL,
    channel               TEXT NOT NULL,   -- offline_exhibition/social_media/overseas_tour/...
    territory             TEXT NOT NULL,
    submitted_by          TEXT NOT NULL,
    interested_parties_json TEXT NOT NULL DEFAULT '[]',
    submitted_at          TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'approved', 'rejected'))
);

-- 审批链（每次裁决追加，不改写）
CREATE TABLE IF NOT EXISTS approvals (
    approval_ref TEXT PRIMARY KEY,
    plan_ref     TEXT NOT NULL REFERENCES plans(plan_ref),
    approver_ref TEXT NOT NULL,
    decision     TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    decided_at   TEXT NOT NULL,
    note         TEXT,
    verdict_json TEXT NOT NULL DEFAULT '{}'  -- 裁决时点的核验快照
);

-- 发布证据：一旦写入不可变，迟到授权/撤回都不改写它
CREATE TABLE IF NOT EXISTS publications (
    publication_ref TEXT PRIMARY KEY,
    plan_ref        TEXT NOT NULL REFERENCES plans(plan_ref),
    version_ref     TEXT NOT NULL REFERENCES versions(version_ref),
    channel         TEXT NOT NULL,
    published_at    TEXT NOT NULL,
    evidence_json   TEXT NOT NULL
);

-- 渠道冻结：撤回只冻结实际受影响的发布渠道
CREATE TABLE IF NOT EXISTS channel_freezes (
    freeze_ref     TEXT PRIMARY KEY,
    withdrawal_ref TEXT NOT NULL REFERENCES withdrawals(withdrawal_ref),
    channel        TEXT NOT NULL,
    frozen_at      TEXT NOT NULL,
    note           TEXT,
    UNIQUE (withdrawal_ref, channel)
);

-- 处置清单：撤回后需要下线/替换的具体发布
CREATE TABLE IF NOT EXISTS disposition_items (
    item_ref        TEXT PRIMARY KEY,
    withdrawal_ref  TEXT NOT NULL REFERENCES withdrawals(withdrawal_ref),
    publication_ref TEXT NOT NULL REFERENCES publications(publication_ref),
    channel         TEXT NOT NULL,
    action          TEXT NOT NULL DEFAULT 'takedown',
    status          TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'done')),
    created_at      TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('002_authorization');
