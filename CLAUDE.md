
## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill
tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.
The skill has specialized workflows that produce better results than ad-hoc answers.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Design system, brand → invoke design-consultation
- Visual audit, design polish → invoke design-review
- Architecture review → invoke plan-eng-review
- Save progress, checkpoint, resume → invoke checkpoint
- Code quality, health check → invoke health

## Porting changes from CancerBot (CB)

CancerBot (`/Users/lm/work/biblum/cancerbot`) is the **primary/upstream** for search & matcher
logic; EXACT is downstream and receives ported changes. The therapy matcher/queryset/config is
~95% identical between the repos — keep it that way.

When porting any search/matcher change from CB, or touching therapy matching / the OMOP fields,
**read and follow [docs/porting-from-cancerbot.md](docs/porting-from-cancerbot.md) first.** Core
rules: port verbatim (no mid-port refactors), keep legacy-mode EXACT byte-identical to CB, and
confine all OMOP divergence to the `TherapyMatchProfile` seam behind the (default-OFF) feature flag.
