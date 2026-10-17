# Repository agent guidance

## Cross-version compatibility policy

> Do not infer chart-semantic incompatibility from game version changes alone.
>
> Reuse previously validated chart semantics, compilation rules, and compatible runtime recipes whenever the relevant contracts remain unchanged.
>
> A generated target inventory describes the recipes bundled in a particular build; it is not a permanent project-wide version support policy.
>
> Never require a full manual chart revalidation solely because the game version changed.

## Applying the policy

- Treat `masterVersion`, `resourceVersion`, catalog versions, and game-version endpoint values as provenance/fingerprint inputs, not as a project-wide support whitelist.
- Monitor snapshots describe observed upstream data; they are not an OnSSM runtime recipe inventory.
- Trigger and classify scans from measured source fingerprints, manifest/catalog hashes, and Master table changes. Preserve immutable snapshots and history.
- Report exact changed or missing data and contract evidence. A changed version label with unchanged relevant content is not evidence of chart-semantic incompatibility.
- Coordinate downstream work through data diffs. Do not require full manual chart revalidation solely because a version changed.
