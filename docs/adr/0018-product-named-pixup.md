# The product is named PixUp

Decided on the code-layout ticket (#20, 2026-10-02) as Vexel; **revised 2026-10-09 to PixUp** — the user exercised the change-of-mind explicitly reserved at close. The product — this whole monorepo (ADR 0006) — is **PixUp**: repository `PixUp`, solution `PixUp.sln`, every project `PixUp.*`, website `pixup.tv`. The repository rename and the local move (contents one level up into `D:\Programming\PixUp`, which already carries the product name; Claude project-context re-keyed for continuity) run as one bundled activity.

The name is a brand, deliberately not a keyword domain. The direction was chosen on the go-to-market economics research ([`docs/research/naming-gtm-economics.md`](../research/naming-gtm-economics.md)): disclosed revenue in this market sits entirely on the brand side (Magnific, Topaz), keyword-domain sites show traffic but no money on pricing that doesn't survive per-minute GPU video costs, and the one-brand-many-offerings roadmap rules out keyword names (one domain per keyword, 6–12 months of SEO each). Keyword-style landing pages inside the brand site do the traffic work instead. Unlike the invented runners-up, PixUp gestures at the domain (pix) without being keyword-locked.

**SeedVR2 is launch content, never identity**: a job type, presets, model files, an offering page — nothing infrastructural carries the model's name. The model-name domains are taken by competitors anyway, and the name belongs to ByteDance.

**Known collision, accepted with eyes open**: "Photo Enhancer: PixUp" is an existing AI photo enhancer app — a name collision inside our exact area. The accepted cost: shared search results with an in-area product and a contested trademark position. The user accepts this knowingly ("if I change my mind later I will rename things later"); before implementation starts a rename is a find-and-replace.

**Runners-up, kept warm** (the user may still switch):

- **Vexel** (`vexel.media`) — the original #20 choice; invented, nothing in upscaling uses it, but crowded elsewhere: vexel.com is a crypto platform, a Vexel TTS Mac app exists, and "vexel" is an established digital-art term that pollutes bare-word search.
- **Murro** — verified clean (no software use found 2026-10-02); the strongest fallback if PixUp's in-area collision becomes a real cost.

## Considered Options

- Vexel, Murro, Trezzle and ~60 generated candidates across ten sound families — see above; Vexel and Murro explicitly retained as fallbacks.
- Keyword/descriptive domain (imgupscaler.ai style) — rejected for the identity (kept for landing pages): keyword-locked, clone-exposed, no trademark protection possible, misfits the umbrella roadmap.
- SeedVR2-derived name/domain — rejected: not ours to own, chained to one model's lifespan, and the good domains are already squatted.
