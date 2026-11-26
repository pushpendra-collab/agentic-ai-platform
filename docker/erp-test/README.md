# InvenLogic TEST replica

A PostgreSQL 18 copy of the **structure** of the company's InvenLogic ERP: the tables, columns, types, defaults and forced RLS the agent reads, taken from the Schema Atlas (2026-10-29). It holds TEST rows only, created by the tests (names end in "(TEST)"). It is **not** the company ERP. Never point production settings at it.

```powershell
docker compose -f docker/erp-test/compose.yml up -d          # port 127.0.0.1:5434
$env:TEST_ERP_ADMIN_URL="postgresql://erp_test_admin:erp_test_admin_dev@127.0.0.1:5434/postgres"
$env:TEST_ERP_READER_URL="postgresql://agentic_erp_reader:erp_reader_dev@127.0.0.1:5434"
.venv\Scripts\python -m pytest
docker compose -f docker/erp-test/compose.yml down -v       # drop it (TEST data only)
```

Without the two variables, every test that needs the replica is skipped.

What is created:
- `init/00-invenlogic.sh`: the three databases (`master_data_db`, `sales_db`, `inventory_db`) with their owner roles, and the agent's read-only role `agentic_erp_reader`. The role has `LOGIN`, `default_transaction_read_only=on`, `CONNECT`, `USAGE` on `public` and `SELECT` on the read tables only.
- `sql/*.sql`: the tables, with `invenlogic_company_id()` reading `invenlogic.company_id`, and RLS enabled and forced on every table.

Tests insert rows as the admin (a superuser, which bypasses RLS). Each test company gets a new `company_id`, so data from runs never mixes. The agent only ever connects as `agentic_erp_reader`. Use `127.0.0.1`, not `localhost` (see docs/erp/INVENLOGIC_MAPPING.md § 6).

## Demo data

To try the chatbot on realistic data, seed one demo company (the replica must be up and the two variables above set):

```powershell
.venv\Scripts\python -m scripts.seed_erp_demo
```

- It creates InvenLogic company **900000001**. Use `900000001` as the platform tenant id in the chatbot.
- The company has 100 customers (2 inactive), credit invoices over the last ~18 months (paid, part paid, unpaid and overdue, plus cancelled and draft ones), opening bills dated 1 April of this financial year, advances, credit notes, daily counter sales from 1 April of last financial year to today, and 25 products in 4 warehouses with stock and reorder rules (8 items low). Some names are ambiguous on purpose, for example "Rahul", "Priya", "Amit Kumar" and the product "iPhone 15".
- This is **TEST data only**: every name ends in "(TEST)". Nothing comes from the company's real ERP, and the script never writes to it.
- The script refuses to run, before writing anything, unless the admin URL is `erp_test_admin` on `127.0.0.1`/`localhost` and the server has the replica's three databases, the `agentic_erp_reader` role and `invenlogic_company_id()` in each database.
- You can run it again at any time. It deletes company 900000001's rows and inserts them again, with the same figures on the same day. Dates are relative to today in Asia/Kolkata.
- At the end it reads the data back as `agentic_erp_reader` through the agent's read path, and prints the figures and some example questions to ask.
