# Hertzon Bench site

The public comparison site of Hertzon Bench (Tools > Solver Benchmark > Hertzon Bench > Submit Results).
One Cloudflare Pages project:

- `public/`: the site (plain HTML, CSS and JavaScript, no build): ranking with filters, single results,
  systems with all their runs, Hertzon versions (all releases from GitHub; those with results link to them and
  compare with the version before on the same hardware), about and privacy.
- `functions/api/`: Pages Functions. `POST /api/submit` checks and stores a submission,
  `GET /api/results` lists them, `GET /api/result/<id>` returns one, `DELETE /api/result/<id>` with the header
  `X-Delete-Key` removes one (the key is shown in Hertzon after submitting).
- `schema.sql`: the D1 database.
- `functions/_lib/suite.js` and `public/suite.json`: what a submission is checked against (the models' file
  hashes, reference times, plausible mesh sizes). Written by `make_suite_spec.py` from
  `hertzon/solver/suite.py`; a test in `tests/test_bench_suite.py` fails when they are out of date.

## What it checks

Structure and value ranges; the suite version; that every test used the suite's model (file hash); a final
mesh between a third and three times the reference; that every result check passed; and it recomputes every
score from the times instead of trusting it. The same run (its random run id) is stored once. At most 10
submissions per address and day; for that only a salted hash of the address is kept, for one day. No IP
addresses or other personal data are stored. It cannot prove that times are honest: implausible entries can be
hidden by hand (below).

## Setting it up (once)

Needs a free Cloudflare account and Node.js.

```sh
cd bench-site
npx wrangler login
npx wrangler d1 create hertzon-bench          # copy the database_id it prints into wrangler.toml
npx wrangler d1 execute hertzon-bench --remote --file=schema.sql
npx wrangler pages project create hertzon-bench --production-branch main
npx wrangler pages secret put RATE_SALT --project-name hertzon-bench     # any long random text
npx wrangler pages deploy
```

The site is then at `https://hertzon-bench.pages.dev` (the address Hertzon sends to; `HERTZON_BENCH_URL`
overrides it). A custom domain can be added in the Cloudflare dashboard under Pages > hertzon-bench > Custom
domains; then change `BENCH_URL` in `hertzon/solver/bench_submit.py`.

## Updating

- After a change of the site: `npx wrangler pages deploy`.
- After a change of the suite (models, reference): raise `SUITE` in `hertzon/solver/suite.py`, run
  `python -m hertzon.solver.suite OUT --make-reference` on the reference computer, copy `OUT/reference.json` to
  `examples/benchmark/suite/`, run `python bench-site/make_suite_spec.py` (it keeps the older suites) and deploy.

## Moderation

```sh
npx wrangler d1 execute hertzon-bench --remote --command "SELECT id, created, cpu, score, name FROM results ORDER BY created DESC LIMIT 50"
npx wrangler d1 execute hertzon-bench --remote --command "UPDATE results SET hidden = 1 WHERE id = 'abcd2345'"
npx wrangler d1 export hertzon-bench --remote --output backup.sql      # backup
```

## Trying it locally

```sh
npx wrangler d1 execute hertzon-bench --local --file=schema.sql
npx wrangler pages dev                                    # http://localhost:8788
HERTZON_BENCH_URL=http://localhost:8788 hertzon           # submit from Hertzon to the local site
```
