-- metricgraph 元数据导出 v2026_03
CREATE TABLE dim_order (
  `order_id`   BIGINT        NOT NULL,  -- fid=1001
  `ord_no`     VARCHAR(64)   NOT NULL,  -- fid=1002
  `order_amt`  DECIMAL(18,2) NOT NULL   -- fid=1004
);
CREATE TABLE fct_pay (
  `pay_id`     BIGINT        NOT NULL,  -- fid=2001
  `order_id`   BIGINT        NOT NULL,  -- fid=2002
  `pay_amt`    DECIMAL(18,2) NOT NULL   -- fid=2003
);
CREATE TABLE dim_user (
  `user_id`    BIGINT        NOT NULL,  -- fid=3001
  `user_name`  VARCHAR(32)   NOT NULL,  -- fid=3002
  `created_at` DATE          NULL       -- fid=3003
);
