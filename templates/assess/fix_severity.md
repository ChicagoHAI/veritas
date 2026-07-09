# Fix Severity Assessment

You are evaluating the fixes that a replication agent applied while reproducing a scientific paper's results. Your job is to rate each fix's severity and assess what it implies about the paper and repository's reproducibility quality.

## Available skills

A catalog of scientific-computing skills is staged at
`{{ skills_dir }}/`. Each subdirectory has a `SKILL.md` whose
YAML frontmatter `description:` field summarizes when the skill applies.
You may browse the catalog if a skill helps you understand a fix's
context; most severity assessments will not need any skill, and that
is fine.

## Fixes Applied

{% for fix in fixes %}
### Fix {{ loop.index }}
- **File:** {{ fix.file_path }}
- **Description:** {{ fix.description }}
- **Original error:** {{ fix.original_error }}
- **Diff:** {{ fix.diff_snippet }}

{% endfor %}

## Your Task

For each fix, assess:

1. **Severity** — one of:
   - `minor`: Routine maintenance (API renames, dependency version pins, import path updates, hardcoded-path corrections). A human would fix this in under a minute.
   - `major`: Significant but localized issue (wrong algorithm parameters, broken data loading, missing preprocessing steps, incompatible library versions requiring code changes). A human would need to understand the code to fix this.
   - `critical`: Fundamental problem (core algorithm is wrong, essential data is unavailable, methodology cannot be implemented as described). Questions whether the paper's results are achievable from the provided code.

2. **Rationale** — why you assigned this severity level.

3. **Reproducibility impact** — what this fix tells us about the paper/repo's quality. For example: "Common Python version drift — does not reflect on the paper's methodology" or "Missing preprocessing step suggests the published code is incomplete."

Also provide a one-paragraph summary of the overall fix burden.

## Output

Save your assessment to `{{ output_dir }}/assess/fix_severity.json`:

```json
{
    "fixes": [
        {
            "fix_description": "Brief description of what was fixed",
            "severity": "minor|major|critical",
            "rationale": "Why this severity",
            "reproducibility_impact": "What this implies about the repo"
        }
    ],
    "summary": "Overall narrative about the fix burden",
    "total_fixes": 0,
    "minor_count": 0,
    "major_count": 0,
    "critical_count": 0
}
```

Begin your assessment now.
