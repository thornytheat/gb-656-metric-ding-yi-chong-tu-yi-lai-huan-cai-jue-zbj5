-- metricgraph 元数据导出 v2026_06（业务库直出，含历史遗留列）
CREATE TABLE dim_order (
  `order_id`   BIGINT        NOT NULL,  -- fid=1001
  `order_no`   VARCHAR(64)   NOT NULL,  -- fid=1002
  `ORDER_NO`   VARCHAR(32)   NULL,      -- fid=1003
  `order_amt`  DECIMAL(18,2) NOT NULL,  -- fid=1004
  `order_amt`  DECIMAL(18,2) NOT NULL, , -- fid=1005
  `status`     VARCHAR(16)   NOT NULL,
  `ext_json`   VARIANT       NULL       -- fid=1007
);
CREATE TABLE fct_pay (
  `pay_id`     BIGINT        NOT NULL,  -- fid=2001
  `order_id`   BIGINT        NOT NULL,  -- fid=2002
  `pay_amt`    DECIMAL(18,2) NOT NULL,  -- fid=2003
  `paid_at`    TIMESTAMP     NULL,      -- fid=2004
  `channel`    VARCHAR(16)   NULL       -- fid=2005
);
CREATE TABLE dim_user (
  `user_id`    BIGINT        NOT NULL,  -- fid=3001
  `user_name`  VARCHAR(40)   NOT NULL,  -- fid=3002
  `created_at` DATE          NULL       -- fid=3003
);
