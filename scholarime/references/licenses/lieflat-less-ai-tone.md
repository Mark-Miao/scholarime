# lieflat-less-ai-tone Attribution

Scholarime's response-level anti-AI-tone checks adapt selected ideas from:

- Project: `lieflat-less-ai-tone`
- Repository: https://github.com/larashero3-dotcom/lieflat-less-ai-tone
- Author: shiujan
- License: Creative Commons Attribution-NonCommercial 4.0 International
- License text: https://creativecommons.org/licenses/by-nc/4.0/

Changes made for Scholarime:

- Reduced the long-form detection, user-confirmation, rewriting, and before/after reporting
  workflow to checks suitable for short final responses.
- Merged the high-signal rules into `references/language-style.md`.
- Added a minimal-edit and information-preservation gate for the review subagent.
- Added a prompt-only direct-delivery wrapper under `references/less-ai-tone/`.
- Included the upstream prompt, detection protocol, semantic criteria, research notes, worked
  example, and license.
- Did not copy the upstream detector scripts or marketing images.

The upstream project does not endorse Scholarime. The adapted material must not be used for
commercial purposes unless the user obtains the permissions required by the upstream license.

The adaptation record remains subject to the upstream CC BY-NC 4.0 terms. The repository-level
CC BY-NC-SA 4.0 license does not replace or broaden that upstream permission.
