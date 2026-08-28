# Graph Report - agent_a0341a14-815e-4283-a95b-c3258309d61b  (2026-08-28)

## Corpus Check
- 393 files · ~478,842 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 40 nodes · 98 edges · 6 communities (5 shown, 1 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `f054c156`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- DelfiIE
- main
- uninstall.sh
- bootstrap.sh
- log_step
- gather_inputs

## God Nodes (most connected - your core abstractions)
1. `main()` - 12 edges
2. `log_step()` - 10 edges
3. `die()` - 10 edges
4. `preflight()` - 10 edges
5. `log_info()` - 9 edges
6. `log_warn()` - 7 edges
7. `gather_inputs()` - 7 edges
8. `dns_setup()` - 7 edges
9. `setup_repo()` - 7 edges
10. `deploy_and_verify()` - 6 edges

## Surprising Connections (you probably didn't know these)
- `deploy_and_verify()` --calls--> `log_info()`  [EXTRACTED]
  deploy/bootstrap.sh → deploy/bootstrap.sh  _Bridges community 1 → community 4_
- `dns_setup()` --calls--> `log_info()`  [EXTRACTED]
  deploy/bootstrap.sh → deploy/bootstrap.sh  _Bridges community 1 → community 3_
- `gather_inputs()` --calls--> `log_info()`  [EXTRACTED]
  deploy/bootstrap.sh → deploy/bootstrap.sh  _Bridges community 1 → community 5_
- `dns_setup()` --calls--> `log_warn()`  [EXTRACTED]
  deploy/bootstrap.sh → deploy/bootstrap.sh  _Bridges community 4 → community 3_
- `gather_inputs()` --calls--> `log_warn()`  [EXTRACTED]
  deploy/bootstrap.sh → deploy/bootstrap.sh  _Bridges community 4 → community 5_

## Import Cycles
- None detected.

## Communities (6 total, 1 thin omitted)

### Community 0 - "DelfiIE"
Cohesion: 0.22
Nodes (7): InfoExtractor, DelfiIE, div_end(), yt-dlp plugin extractor for Delfi (Estonian news portal) article videos. Delfi…, Return the index past the closing </div> of the opened div at `start`., Extractor for Delfi articles that embed a JWPlayer-hosted video., Return (manifest_url, video_id) or (None, None).

### Community 1 - "main"
Cohesion: 0.46
Nodes (8): die(), install_docker(), log_info(), main(), require_root(), setup_caddy(), setup_environment(), bootstrap.sh script

### Community 2 - "uninstall.sh"
Cohesion: 0.54
Nodes (7): confirm(), die(), log_error(), log_info(), log_warn(), remove_local_stack(), uninstall.sh script

### Community 3 - "bootstrap.sh"
Cohesion: 0.52
Nodes (6): cloudflare_api(), confirm(), dns_setup(), is_valid_ipv4(), log_error(), preflight()

### Community 4 - "log_step"
Cohesion: 0.60
Nodes (5): deploy_and_verify(), log_step(), log_warn(), setup_repo(), summary()

## Knowledge Gaps
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `main()` connect `main` to `bootstrap.sh`, `log_step`, `gather_inputs`?**
  _High betweenness centrality (0.021) - this node is a cross-community bridge._
- **Why does `preflight()` connect `bootstrap.sh` to `main`, `log_step`, `gather_inputs`?**
  _High betweenness centrality (0.015) - this node is a cross-community bridge._