"""Pipeline state tracker for resumable evaluation phases.

Persists per-stage status, timestamps, and arbitrary per-stage outputs to
``<output_dir>/.veritas/pipeline_state.json``. ``ReplicationRunner`` consults
the tracker before each phase so completed phases are skipped on re-run.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


STATE_DIR = ".veritas"
STATE_FILE = "pipeline_state.json"


class PipelineState:
    """Tracks pipeline execution state for resumable phases."""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.state_file = self.output_dir / STATE_DIR / STATE_FILE
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        if self.state_file.exists():
            with open(self.state_file, 'r', encoding='utf-8') as f:
                self.state = json.load(f)
        else:
            self.state = {
                'created_at': datetime.now().isoformat(),
                'inputs': None,
                'stages': {},
                'current_stage': None,
                'completed': False,
            }
            self._save()

    def _save(self) -> None:
        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, indent=2)

    # -- Stage transitions ---------------------------------------------------

    def start_stage(self, name: str) -> None:
        self.state['current_stage'] = name
        self.state['stages'][name] = {
            'status': 'in_progress',
            'started_at': datetime.now().isoformat(),
            'completed_at': None,
            'success': None,
            'outputs': {},
        }
        self._save()

    def complete_stage(
        self,
        name: str,
        success: bool,
        outputs: Optional[Dict[str, Any]] = None,
    ) -> None:
        if name not in self.state['stages']:
            self.state['stages'][name] = {}

        self.state['stages'][name].update({
            'status': 'completed' if success else 'failed',
            'completed_at': datetime.now().isoformat(),
            'success': success,
            # preserve outputs accumulated via update_stage_outputs (e.g. completed_categories) when caller passes outputs=None
            'outputs': outputs if outputs is not None
            else self.state['stages'][name].get('outputs', {}),
        })
        self.state['current_stage'] = None
        self._save()

    def update_stage_outputs(self, name: str, outputs: Dict[str, Any]) -> None:
        """Merge outputs into a stage that's still in_progress.

        Used for per-category sub-completion in the ``evaluate`` stage —
        each completed category is appended to the stage's outputs without
        marking the whole stage complete.
        """
        if name not in self.state['stages']:
            self.state['stages'][name] = {
                'status': 'in_progress',
                'started_at': datetime.now().isoformat(),
                'completed_at': None,
                'success': None,
                'outputs': {},
            }
        existing = self.state['stages'][name].setdefault('outputs', {})
        existing.update(outputs)
        self._save()

    def mark_completed(self) -> None:
        self.state['completed'] = True
        self.state['completed_at'] = datetime.now().isoformat()
        self._save()

    # -- Stage queries -------------------------------------------------------

    def get_stage_status(self, name: str) -> Optional[str]:
        return self.state['stages'].get(name, {}).get('status')

    def is_stage_completed(self, name: str) -> bool:
        stage = self.state['stages'].get(name, {})
        return stage.get('status') == 'completed' and stage.get('success', False)

    def get_stage_outputs(self, name: str) -> Dict[str, Any]:
        return self.state['stages'].get(name, {}).get('outputs', {}) or {}

    # -- Input fingerprinting ------------------------------------------------

    def record_inputs(
        self,
        repo_path: Path,
        paper_path: Optional[Path],
    ) -> None:
        """Stash inputs into ``state['inputs']`` on first run."""
        self.state['inputs'] = {
            'repo_path': str(Path(repo_path).resolve()),
            'paper_path': str(Path(paper_path).resolve()) if paper_path else None,
            'paper_sha256': _sha256_of_file(paper_path) if paper_path else None,
        }
        self._save()

    def validate_inputs(
        self,
        repo_path: Path,
        paper_path: Optional[Path],
    ) -> bool:
        """Return True if inputs match the recorded run; False (and print warning) otherwise.

        Does not abort — the caller decides what to do with a False result.
        """
        recorded = self.state.get('inputs')
        if recorded is None:
            return True

        current_repo = str(Path(repo_path).resolve())
        current_paper = str(Path(paper_path).resolve()) if paper_path else None
        current_sha = _sha256_of_file(paper_path) if paper_path else None

        mismatches = []
        if recorded.get('repo_path') != current_repo:
            mismatches.append(f"repo_path: {recorded.get('repo_path')}  ->  {current_repo}")
        if recorded.get('paper_path') != current_paper:
            mismatches.append(f"paper_path: {recorded.get('paper_path')}  ->  {current_paper}")
        if recorded.get('paper_sha256') != current_sha:
            mismatches.append(f"paper_sha256: {recorded.get('paper_sha256')}  ->  {current_sha}")

        if mismatches:
            print("WARNING: inputs differ from previous run. Resuming anyway:")
            for m in mismatches:
                print(f"     {m}")
            print("   Pass --restart if this isn't what you want.")
            return False
        return True


def _sha256_of_file(path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    p = Path(path)
    if not p.exists():
        return None
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()
