-- WORK-623 研报正文（发现报告高级 VIP）规范化层 schema
--
-- 来源：/srv/share/quantdb/raw/reports_fulltext/fxbaogao/<YYYY-MM-DD>/<report_id>-<slug>.pdf
--       + 同名 meta.json（由 scripts/fxbaogao_fulltext.py 采集/登记）
-- 采集与入库都在 Mo-server：采集走官方 API（scripts/fxbaogao_fulltext.py），入库走 ETL 进程；
-- 本文件只建正文层对象，不触碰既有 20 张 A 股表、report_meta 与行业层。
--
-- 约定（与 db/schema.sql / db/schema_industry.sql 一致）：
--   * 唯一键 = 发现报告 detail 数字 id（report_id），同 id 重抓覆盖不新增；
--   * sha256 入表：内容变了写一行 report_fulltext_changes 留痕，再覆盖正文行；
--   * 标题/机构的比较口径写在 SQL 宏里（fx_norm_title / fx_norm_org），
--     入库脚本不另算一份归一化，避免两处口径漂移；
--   * 与 report_meta 的关联只在视图里做，不回写 report_meta。

PRAGMA threads=4;

-- ---------------------------------------------------------------------------
-- 归一化宏：标题、机构
-- ---------------------------------------------------------------------------
-- 站点标题形如「艾奇在线广告研究院:《2026中国教育行业GEO与SEO优化研究报告：…》-发现报告」，
-- report_meta 标题形如「2026中国教育行业GEO与SEO优化研究报告：…」，机构形如「开源证券股份有限公司」。
-- 判定顺序：去掉站点后缀 -> 去标点空白并小写 -> 去掉开头机构名 -> 去机构公司后缀。

-- 宏按依赖顺序定义：DuckDB 在 CREATE MACRO 时校验被引用函数存在。
CREATE OR REPLACE MACRO fx_strip_site(s) AS
    regexp_replace(coalesce(s, ''), '(发现报告网?|fxbaogao|报告网)$', '');

CREATE OR REPLACE MACRO fx_strip_text(s) AS
    lower(regexp_replace(
        coalesce(s, ''),
        '[（）()《》〈〉【】\[\]{}「」『』:：;；,，.。!！?？、\s\-—_·"“”‘’''/\\|+*]',
        '', 'g'));

CREATE OR REPLACE MACRO fx_norm_org(s) AS
    fx_strip_text(regexp_replace(
        coalesce(s, ''), '(集团)?(股份有限公司|有限责任公司|有限公司)$', ''));

-- 站点标题去掉结尾「-发现报告」后，再去掉开头机构名（站点把机构写在冒号前）。
-- 机构原文可能带「有限责任公司」这类后缀，而站点有时整串写进标题、有时只写简称，
-- 因此先试整串前缀，再试去后缀的前缀。
CREATE OR REPLACE MACRO fx_norm_title(title, org) AS
    CASE
        WHEN fx_strip_text(org) <> ''
         AND starts_with(fx_strip_site(fx_strip_text(title)), fx_strip_text(org))
            THEN substr(fx_strip_site(fx_strip_text(title)), length(fx_strip_text(org)) + 1)
        WHEN fx_norm_org(org) <> ''
         AND starts_with(fx_strip_site(fx_strip_text(title)), fx_norm_org(org))
            THEN substr(fx_strip_site(fx_strip_text(title)), length(fx_norm_org(org)) + 1)
        ELSE fx_strip_site(fx_strip_text(title))
    END;

-- ---------------------------------------------------------------------------
-- 1. 正文主表（一篇报告一行，唯一键 report_id）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS report_fulltext (
    "report_id"   BIGINT    NOT NULL,
    "source"      VARCHAR   NOT NULL DEFAULT 'fxbaogao',
    "title"       VARCHAR   NOT NULL,
    "org_name"    VARCHAR,
    "publish_date" DATE,
    "page_url"    VARCHAR,
    "site_url"    VARCHAR,
    "pdf_url"     VARCHAR,
    "pdf_path"    VARCHAR   NOT NULL,
    "pdf_bytes"   BIGINT    NOT NULL,
    "pdf_pages"   INTEGER   NOT NULL,
    "sha256"      VARCHAR   NOT NULL,
    "fetched_at"  TIMESTAMP,
    "fetch_mode"  VARCHAR   NOT NULL,
    "meta_path"   VARCHAR,
    "ingested_at" TIMESTAMP NOT NULL,
    PRIMARY KEY ("report_id")
);

-- 已存在的旧表（WORK-623 首版只落 pdf_path）补列；ADD COLUMN IF NOT EXISTS 幂等。
ALTER TABLE report_fulltext ADD COLUMN IF NOT EXISTS site_url VARCHAR;
ALTER TABLE report_fulltext ADD COLUMN IF NOT EXISTS pdf_url VARCHAR;

COMMENT ON TABLE report_fulltext IS '研报正文 PDF 的元数据层；一篇报告一行，唯一键为发现报告 detail 数字 id。正文本身不进库，只在磁盘 raw/reports_fulltext 下';
COMMENT ON COLUMN report_fulltext.report_id IS '发现报告 detail 数字 id（https://www.fxbaogao.com/detail/<id> 的 <id>）';
COMMENT ON COLUMN report_fulltext.title IS '站点详情页标题原文（含「-发现报告」后缀，原样保存）';
COMMENT ON COLUMN report_fulltext.org_name IS '机构/作者原文，站点写法（如「开源证券」）';
COMMENT ON COLUMN report_fulltext.publish_date IS '站点 og:published_time 的日期（CST）';
COMMENT ON COLUMN report_fulltext.page_url IS '详情页 URL（不用 /pdf 路径）';
COMMENT ON COLUMN report_fulltext.site_url IS '官网回查链接 https://www.fxbaogao.com/view?id=<report_id>（官方要求每篇保留）';
COMMENT ON COLUMN report_fulltext.pdf_url IS '官方 download 接口返回的 PDF 地址（可能带时效签名，仅作证据）';
COMMENT ON COLUMN report_fulltext.pdf_path IS 'PDF 绝对路径：raw/reports_fulltext/fxbaogao/<page_date>/<report_id>-<slug>.pdf';
COMMENT ON COLUMN report_fulltext.pdf_bytes IS 'PDF 文件字节数（入库时实测）';
COMMENT ON COLUMN report_fulltext.pdf_pages IS 'PDF 页数（pypdf 读出，必须 > 0）';
COMMENT ON COLUMN report_fulltext.sha256 IS 'PDF 文件 sha256（小写十六进制）；内容变化时另写 report_fulltext_changes';
COMMENT ON COLUMN report_fulltext.fetch_mode IS '抓取方式：api=官方接口（本单）；旧值 cdp/manual 仅历史行';
COMMENT ON COLUMN report_fulltext.meta_path IS '同名 meta.json 的绝对路径';

-- ---------------------------------------------------------------------------
-- 2. 内容变更留痕（同 id 不同 sha256 时一行）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS report_fulltext_changes (
    "report_id"    BIGINT    NOT NULL,
    "changed_at"   TIMESTAMP NOT NULL,
    "old_sha256"   VARCHAR,
    "new_sha256"   VARCHAR   NOT NULL,
    "old_pdf_path" VARCHAR,
    "new_pdf_path" VARCHAR   NOT NULL,
    "reason"       VARCHAR   NOT NULL,
    PRIMARY KEY ("report_id", "changed_at")
);

COMMENT ON TABLE report_fulltext_changes IS '正文内容变更留痕：同 report_id 的 sha256 变化时追加一行，用于「内容变了能看出来」';

-- ---------------------------------------------------------------------------
-- 3. 阅读视图（检索入口）
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_report_fulltext AS
SELECT
    f.report_id,
    f.source,
    f.title,
    fx_norm_title(f.title, f.org_name) AS title_norm,
    f.org_name,
    fx_norm_org(f.org_name)           AS org_norm,
    f.publish_date,
    f.page_url,
    f.site_url,
    f.pdf_url,
    f.pdf_path,
    f.pdf_bytes,
    f.pdf_pages,
    f.sha256,
    f.fetched_at,
    f.fetch_mode,
    f.ingested_at
FROM report_fulltext f;

COMMENT ON VIEW v_report_fulltext IS '正文检索视图：原字段 + 归一化标题/机构（归一化口径与关联视图共用 SQL 宏）';

-- ---------------------------------------------------------------------------
-- 4. 与 report_meta 的关联候选（标题归一 + 机构归一 + 发布日 ±3 天）
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_report_fulltext_link_candidates AS
WITH cand AS (
    SELECT
        f.report_id,
        m.info_code,
        m.title              AS meta_title,
        m.org_name           AS meta_org_name,
        m.publish_date       AS meta_publish_date,
        abs(date_diff('day', f.publish_date, m.publish_date)) AS day_gap,
        count(*) OVER (PARTITION BY f.report_id)              AS candidate_count
    FROM report_fulltext f
    JOIN report_meta m
      ON fx_norm_title(f.title, f.org_name) = fx_norm_title(m.title, m.org_name)
     AND fx_norm_title(f.title, f.org_name) <> ''
     AND f.publish_date IS NOT NULL
     AND m.publish_date IS NOT NULL
     AND abs(date_diff('day', f.publish_date, m.publish_date)) <= 3
    WHERE fx_norm_org(f.org_name) <> ''
      AND fx_norm_org(f.org_name) = fx_norm_org(m.org_name)
)
SELECT * FROM cand;

COMMENT ON VIEW v_report_fulltext_link_candidates IS '正文 × report_meta 的候选匹配：标题归一相等 + 机构归一相等 + 发布日相差 ≤3 天；一篇正文可有多行候选';

-- ---------------------------------------------------------------------------
-- 5. 关联结果（每篇正文取一条：日差最小，其次 info_code 最小）
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_report_fulltext_linked AS
SELECT * FROM v_report_fulltext_link_candidates
QUALIFY row_number() OVER (PARTITION BY report_id ORDER BY day_gap, info_code) = 1;

COMMENT ON VIEW v_report_fulltext_linked IS '每篇正文一条关联结果；candidate_count > 1 表示同标题多份报告，按日差最小/ info_code 最小取一条（确定性）';

-- ---------------------------------------------------------------------------
-- 6. 未关联正文（运维视角：为什么没配上）
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_report_fulltext_unlinked AS
SELECT f.*
FROM v_report_fulltext f
LEFT JOIN v_report_fulltext_linked l USING (report_id)
WHERE l.report_id IS NULL;

COMMENT ON VIEW v_report_fulltext_unlinked IS '未匹配到 report_meta 的正文；多半是站点独家/媒体报告，或机构名口径不同，不代表入库失败';
