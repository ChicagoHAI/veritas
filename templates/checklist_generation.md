# Checklist Generation for Replication Evaluation

You are generating a personalized evaluation checklist for assessing the quality and reproducibility of a code repository.
{% if has_paper %}
You have been given the paper that this repository is meant to replicate. Generate specific YES/NO questions tailored to this paper and repository.

## Paper

You MUST read the PDF directly from this local path:
{{ paper_path }}

{% else %}
No paper was provided. Generate YES/NO questions based on what you find in the repository — its documentation, code, and stated goals.
{% endif %}

## Repository Path

{{ repo_path }}

## Task Details

Your task is to generate an evaluation checklist of YES/NO questions organized under five categories. Each question should assess a specific, verifiable aspect of the repository.

Checklist questions should:
- **Be answerable by 'yes' or 'no'**, with 'yes' meaning the repository successfully meets the requirement.
- **Be comprehensive, but concise** — cover all criteria directly relevant to this repository, but only include clearly relevant questions.
- **Be precise** — avoid vague wording. Reference specific methods, parameters, file names, or results.{% if has_paper %} Use the paper's own terminology and reference specific sections where appropriate.{% endif %}
- **Be verifiable** — each question should be answerable by reading or running the code, not by subjective judgment.
- **Always phrase positively** — every question MUST be phrased so that YES = good (requirement met) and NO = bad (requirement not met). NEVER phrase a question where YES indicates a problem, inconsistency, or failure.

**BAD (inverted polarity — do NOT do this):**
- "Does the model use only method A, which would be inconsistent with the paper's claim that both A and B are used?" (YES = inconsistency found = bad)
- "Is the learning rate missing from the config?" (YES = missing = bad)

**GOOD (correct polarity):**
- "Does the model combine both method A and method B, as claimed in the paper?" (YES = correctly implemented = good)
- "Is the learning rate specified in the config?" (YES = present = good)

You should first analyze the {% if has_paper %}paper and {% endif %}repository before generating your checklist.

## Categories and Guidance

Generate 3-8 YES/NO questions for each category below. Use the guidance to understand what kinds of questions belong in each category, but make the actual questions specific to THIS repository.

### Code Quality
Assess whether the code is functional and correct:
- C1 guidance: Is the core analysis code runnable without errors?
- C2 guidance: Do implementations correctly match what is described{% if has_paper %} in the paper{% endif %}?
- C3 guidance: Is there unnecessary duplicate code?
- C4 guidance: Does all code contribute to the project's objectives?

### Consistency
Assess alignment between documentation, code, and claims within the repository:
- CS1 guidance: Do results in the repo's documentation match what the code actually produces?
- CS2 guidance: Does the implementation follow the plan/methodology?
- CS3 guidance: Are reported effects non-trivial (not within noise)?
- CS4 guidance: Are key design choices explicitly justified?
- CS5 guidance: Do key results include uncertainty measures or statistical tests?

### Generalization
Assess whether the findings generalize beyond the original experimental setup:
- GT1 guidance: Could the finding hold on models not in the original work?
- GT2 guidance: Could the finding hold on new data not in the original dataset?
- GT3 guidance: If a new method was proposed, could it apply to similar tasks?

### Replication
Assess how well the experiment can be reproduced:
- RP1 guidance: Can the implementation be reconstructed from documentation alone?
- RP2 guidance: Is the environment reproducible (dependencies documented, restorable)?
- RP3 guidance: Are results deterministic and stable across runs?

### Instruction Following
Assess alignment between the stated goals and the implementation:
- TS1 guidance: Does the implementation serve the stated research objective?
- TS2 guidance: Are all methodology steps implemented?
- TS3 guidance: Are all hypotheses tested?
- TS4 guidance: Do code components match their described functions?

## Examples

(1)
### Paper
A study on sentiment analysis using BERT fine-tuning. Reports 92.3% accuracy on SST-2 with learning rate 2e-5, batch size 32, 3 epochs. Compares against LSTM baseline (87.1%).

### Analysis
The paper fine-tunes BERT on SST-2 for sentiment analysis. Key implementation details: learning rate 2e-5, batch size 32, 3 epochs. Claims 92.3% accuracy and improvement over LSTM baseline at 87.1%. Should verify these specific parameters and results in the code.

### Checklist
Code Quality:
- Does the training script execute without errors on the SST-2 dataset?
- Does the code implement BERT fine-tuning (not a different architecture)?
- Does the LSTM baseline implementation correctly use the same data preprocessing pipeline?

Consistency:
- Does the training configuration use learning rate 2e-5, batch size 32, and 3 epochs as stated in the paper?
- Does the reported accuracy in any output logs or README match the 92.3% claim?
- Is the LSTM baseline accuracy reported alongside the BERT result for comparison?

Replication:
- Are package versions (transformers, torch) pinned in requirements.txt or equivalent?
- Does the code set a random seed for reproducible results?

(2)
### Paper
A physics simulation paper implementing a novel N-body force calculation with median fractional force error of 1.2e-05 on 65536 particles. Uses C++ with CUDA acceleration.

### Analysis
The paper presents a novel N-body force calculation algorithm. Key details: 65536 particles, median fractional force error 1.2e-05, C++/CUDA implementation. Should verify the algorithm implementation, particle count, error metric, and GPU utilization.

### Checklist
Code Quality:
- Does the C++/CUDA code compile without errors?
- Does the force calculation implement the algorithm described in the paper (not a standard library call)?
- Is the particle initialization code consistent with the random distribution described in the methods section?

Consistency:
- Does the simulation use exactly 65536 particles as specified?
- Is the median fractional force error computed using the formula defined in the paper?
- Does the output error value fall within the expected range of the reported 1.2e-05?

Generalization:
- Can the force calculation handle a different particle count (e.g., 32768) without code changes?

Replication:
- Are CUDA toolkit version and GPU requirements documented?
- Does a Makefile or build script exist that compiles the code from source?

## Real Task

{% if has_paper %}Read the paper at the path above and explore{% else %}Explore{% endif %} the repository at the specified path. Then:

1. **Analyze**: Identify the {% if has_paper %}paper's key claims, methods, parameters, datasets, and expected results. Note what{% else %}repository's goals, methods, and stated results from its documentation. Note what{% endif %} the repository contains.
2. **Generate**: Produce 3-8 YES/NO questions per category, following the guidance above.

Remember: each question should be phrased such that answering 'yes' means the repository **successfully** meets that requirement. Be specific — use exact parameter values, method names, and file references.

## Output

Save the checklist to `{{ output_dir }}/checklist.json` with this format:

```json
{
    "categories": {
        "code": [
            {"question": "Your specific YES/NO question here?"}
        ],
        "consistency": [
            {"question": "Your specific YES/NO question here?"}
        ],
        "generalization": [
            {"question": "Your specific YES/NO question here?"}
        ],
        "replication": [
            {"question": "Your specific YES/NO question here?"}
        ],
        "instruction": [
            {"question": "Your specific YES/NO question here?"}
        ]
    }
}
```

Also print the JSON to stdout so it can be captured.

Begin your analysis now.
