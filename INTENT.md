# Intent

Why this fork of DeepSeek Harness exists, what must never happen to it, and where it is going. This tree is the house fork of `deepseek-ai/deepseek-harness`: upstream owns the product, this fork owns operating it as a fleet platform. Procedure lives in `docs/OPERATIONS.md` and `AGENTS.md`, never here.

## Purpose

This fork runs DeepSeek Harness as a dependable fleet service, a production deployment that operators and coding agents rely on daily, where availability, predictable behaviour, and a safe rollback outrank arriving at new upstream features early.

It keeps upstream upgrades tractable. The fork holds a thin, reviewable delta on pinned upstream releases, so each harness bump is a small verified step rather than an archaeology dig.

It wires the harness into fleet infrastructure: the LiteLLM gateway as a model route, Hindsight long-term memory, subscription-OAuth providers, and browser access over LAN and VPN.

It carries house features upstream lacks. Automatic LAN/VPN authentication and remote settings administration are additions to the product itself, not merely deployment glue.

## Top invariant

Production never executes from the development tree. Nothing the running service executes, resolves, or falls back to may be a path in the development tree, however it reaches production.

A deployed release is always reversible. No upgrade may leave the service in a state that cannot be returned to a known-good one.

Credentials survive every maintenance act. The browser-session signing secret and the OAuth credential stores are never rotated, overwritten, or deleted as a side effect of a model refresh, a plugin change, or an upgrade.

The fork stays a thin delta on upstream. A change to an upstream-owned file carries a reason and stays surgical, so the tree never drifts into a private product that cannot follow upstream.

Compatibility is proven at runtime, never inferred. A plugin or harness claim comes from a booted profile answering a real request, not from package metadata or a version range nothing enforces. What is proven is the capability surface users actually consume, not merely that a route registers and answers: a check that cannot fail when a capability disappears is not proof of compatibility.

## Non-goals

This is not a redesign of upstream DeepSeek Harness. Upstream owns product design, architecture, and the plugin model; a house change that rearchitects an upstream package is out of scope and belongs upstream instead.

This is not a venue for ad-hoc production hotfixes. Nothing is edited in place under the production root to make a symptom disappear. A fix lands as a commit in the development tree and reaches production through a deploy, so the running release always maps to a known commit.

This is not a maintainer of third-party plugins. Plugins are consumed at an exact pin and verified at runtime; a plugin defect goes to its own upstream rather than being patched here.

This is not a public fork competing with upstream. The tree exists for one operator's fleet and is not published or packaged as an alternative distribution.

## Audience

The operator running the fleet, who decides upgrades and approves deploys and needs the runbook, the rollback path, and the recorded limits to be trustworthy.

Coding agents working in this tree, which must judge a change against this intent, keep the house delta thin, and respect the production boundary without being told each time.

## Core bet

DeepSeek Harness earns a place alongside the fleet's other agent harnesses as one agents genuinely run in. That is what justifies maintaining a service, remote access, and a deploy pipeline rather than running the published package ad hoc.

That bet rides on a second one: upstream stays rideable despite being a developer preview that promises compatibility breaks. A thin delta, a release-snapshot deploy boundary, and runtime verification are together expected to be enough. They stop being enough if upstream churn outpaces the fork's ability to re-verify, at which point the platform freezes on an old release.

## Direction

Upgrade the development tree before production, and treat every deploy as a separate, deliberate decision that the upgrade does not authorize.

Prune house cruft on every bump: drop dependencies nothing imports, and re-justify each patch to an upstream-owned file, removing any that upstream has since made unnecessary.

Keep the runbook truthful. Claims that no longer match live state are corrected, and every documented behaviour is verified against the running platform rather than carried forward.

Move closer to upstream's cadence, bumping often enough that no single upgrade is ever a multi-thousand-file jump again.

## Posture

Verify at runtime and trust nothing declarative. Whether something works is settled by running it.

Prefer reversibility over speed. A change to the running platform keeps a way back, and that outranks deploying sooner.

State only verified claims in documentation, qualify what was not verified, and record known defects openly instead of hiding them.

Intervene minimally in upstream code. Patches stay surgical and source-preserving, keeping upstream's structure and naming so the next merge stays small.

Track upstream aggressively, accepting pre-release churn as the price of never facing a large jump again. Aggressive tracking protects the availability that Purpose ranks first: a service that must stay up is safer under frequent small verified bumps than under one infrequent jump whose blast radius no test pass can bound.
