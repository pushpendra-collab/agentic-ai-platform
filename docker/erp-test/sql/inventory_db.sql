-- InvenLogic inventory_db: the tables the agent reads, as the Schema Atlas (2026-10-29,
-- Flyway V24) lists them: same names, columns, types, NULLs and defaults. TEST replica only:
-- never the real ERP.

CREATE OR REPLACE FUNCTION public.invenlogic_company_id() RETURNS bigint
    LANGUAGE sql STABLE
    AS $fn$ SELECT NULLIF(current_setting('invenlogic.company_id', true), '')::bigint $fn$;

CREATE TABLE public.stock_balance (
    company_id bigint NOT NULL,
    branch_id bigint NOT NULL,
    created_by bigint NOT NULL,
    updated_by bigint NOT NULL,
    warehouse_id bigint NOT NULL,
    product_id bigint NOT NULL,
    quantity_on_hand numeric(18,3) NOT NULL DEFAULT 0,
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, branch_id, warehouse_id, product_id)
);

CREATE TABLE public.reorder_rule (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id bigint NOT NULL,
    branch_id bigint NOT NULL,
    created_by bigint NOT NULL,
    updated_by bigint NOT NULL,
    warehouse_id bigint NOT NULL,
    product_id bigint NOT NULL,
    min_qty numeric(19,3) NOT NULL,
    max_qty numeric(19,3),
    order_uom_id bigint,
    active boolean NOT NULL DEFAULT true,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    updated_at timestamp with time zone NOT NULL DEFAULT now(),
    version bigint NOT NULL DEFAULT 0,
    set_in_uom_id bigint,
    lead_time_days smallint,
    safety_qty numeric(19,3),
    cover_days smallint,
    rounding character varying(8)
);

DO $rls$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['stock_balance', 'reorder_rule'] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON public.%I USING (company_id = invenlogic_company_id())'
            ' WITH CHECK (company_id = invenlogic_company_id())', t);
        EXECUTE format('ALTER TABLE public.%I OWNER TO inventory_owner', t);
        EXECUTE format('GRANT SELECT ON public.%I TO agentic_erp_reader', t);
    END LOOP;
END
$rls$;
