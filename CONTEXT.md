# SeedVr.Backend

Production backend for the SeedVR2 video-upscaling service: accepts enhancement jobs over an API, runs them on Vast.ai GPU workers via ComfyUI, and delivers the results. Successor and future source of truth for the SeedVr proof of concept; its glossary starts from the POC's `CONTEXT.md` with the changes below.

## Language

### Parameters

**Preset**:
A backend-owned, named parameter set. A request carries either a preset reference or raw parameters; the backend resolves a preset into raw parameters before the job runs. Supersedes the POC rule that requests only ever carry raw parameters.
_Avoid_: profile, tier

**Effective parameter set**:
The values a run actually used after the workflow template, request parameters (raw or preset-resolved) and any overrides are layered; recorded with the job.
