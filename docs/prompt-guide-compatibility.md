# Prompt-guide compatibility suite

Virector converts reusable multimodal directing patterns into a private
DirectorPlan. The suite in `tests/test_prompt_guide_compatibility.py` uses
compact original fixtures inspired by common prompt-guide structures; it does
not make the user learn a special form or expose the compiled plan in Studio.

## Current coverage

| Prompt family | Compiler status | Verified output |
| --- | --- | --- |
| Subject, motion, environment, camera and sound | Supported | Timed action and sound cues |
| Multi-angle character images | Supported | Grouped identity and wardrobe binding |
| Multi-image characters, outfit, location and logo | Supported | Separate role-aware bindings |
| Storyboard plus character references | Supported | Composition order, dialogue and continuity |
| Camera/effect image references | Supported | Correct controls without false identity roles |
| Timed dialogue, messages, transitions and title cards | Supported | Worker-ready segment fields |
| Video action, camera and effect references | Planned | Strict expected-failure contract |
| Audio voice and rhythm references | Planned | Strict expected-failure contract |
| Video editing, extension and track completion | Planned | Requires video transport and editing workers |

Strict expected failures are intentional executable backlog. They must become
passing tests when video and audio OmniAsset ingestion is implemented; an
unexpected pass fails CI so incomplete support cannot be mistaken for a
finished capability.

## Model boundary

These tests verify prompt interpretation and routing. They do not claim that a
generation backend can reproduce every requested control. Each passing binding
still needs a compatible identity, motion, camera, speech, lip-sync, audio or
editing model in the production worker graph.

