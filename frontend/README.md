# 1. Knowledge Graph Performance Test Matrix

| Payload Size | Scenario | Expected Result | Pass Criteria |
|---|---|---|---|
| 1k nodes | Load summary, expand 2-3 ego networks, switch Table/Network views | Smooth interactions in both views | No dropped interactions; no UI freeze; successful view switches and search focus |
| 10k nodes | Progressive ego loading + active search/filter + table scroll | UI remains responsive with LOD hiding labels/edges as zoom decreases | Main thread stays responsive; virtualization avoids DOM blowup; ForceAtlas2 worker does not block UI |
| 50k nodes | Low Resource Mode ON, one-shot layout, deep zoom/pan stress | App remains usable with reduced visual fidelity | No renderer crash; camera interactions stay functional; memory growth remains bounded during session |