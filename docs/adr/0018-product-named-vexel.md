# The product is named Vexel

Decided on the code-layout ticket (#20, 2026-10-02). The product — this whole monorepo (ADR 0006) — is **Vexel**: repository `Vexel`, solution `Vexel.sln`, every project `Vexel.*`, website `vexel.media`. The repository rename and the local move (the repo folder relocated to `D:\Programming\Vexel`, retiring the old `PixUp\SeedVr.Backend` nesting — amended 2026-10-03: the parent folder carried the old product name and held nothing else; Claude project-context re-keyed for continuity) run as one bundled activity right after #20 closes.

The name is an invented brand, deliberately not descriptive of upscaling. The direction was chosen on the go-to-market economics research ([`docs/research/naming-gtm-economics.md`](../research/naming-gtm-economics.md)): disclosed revenue in this market sits entirely on the brand side (Magnific, Topaz), keyword-domain sites show traffic but no money on pricing that doesn't survive per-minute GPU video costs, and the one-brand-many-offerings roadmap rules out keyword names (one domain per keyword, 6–12 months of SEO each). Keyword-style landing pages inside the brand site do the traffic work instead.

**SeedVR2 is launch content, never identity**: a job type, presets, model files, an offering page — nothing infrastructural carries the model's name. The model-name domains are taken by competitors anyway, and the name belongs to ByteDance.

**Known collisions, accepted with eyes open**: vexel.com is a crypto platform (never ours; habitual .com-typers will misland), a Vexel text-to-speech Mac app exists, "vexel" is an established digital-art term (vector+pixel raster technique) that pollutes bare-word search. Nothing in upscaling uses the name. The accepted cost: slower ownership of the search results page and a weaker trademark position than a clean name.

**Runners-up, kept warm at the user's request** (the user may still switch; before implementation starts a rename is a find-and-replace):

- **PixUp** (`pixup.tv`) — disqualified-ish: "Photo Enhancer: PixUp" is an existing AI photo enhancer, a collision inside our exact area.
- **Murro** — verified clean (no software use found 2026-10-02); the strongest fallback if Vexel's crowd becomes a real cost.

## Considered Options

- PixUp, Murro, Trezzle and ~60 generated candidates across ten sound families — see above; Murro explicitly retained as fallback.
- Keyword/descriptive domain (imgupscaler.ai style) — rejected for the identity (kept for landing pages): keyword-locked, clone-exposed, no trademark protection possible, misfits the umbrella roadmap.
- SeedVR2-derived name/domain — rejected: not ours to own, chained to one model's lifespan, and the good domains are already squatted.
