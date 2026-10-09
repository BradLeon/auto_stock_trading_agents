"""Check declared dependency IDs, DAG and the prerequisites of task 3.9."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TASKS = ROOT / 'openspec/changes/implement-phase-f-shadow-run-and-cutover/tasks.md'


def main():
    rows = {}
    for checked, identity, body in re.findall(r'^- \[([ x])\] (\d+\.\d+) (.+)$', TASKS.read_text(), re.M):
        dependency = re.search(r'（依赖：([^）]+)）', body)
        rows[identity] = {'complete': checked == 'x',
                          'dependencies': re.findall(r'\d+\.\d+', dependency[1]) if dependency else []}
    visiting, visited = set(), set()
    def visit(identity):
        assert identity in rows, f'unknown dependency: {identity}'
        assert identity not in visiting, f'cycle: {identity}'
        if identity in visited:
            return
        visiting.add(identity)
        for dependency in rows[identity]['dependencies']:
            visit(dependency)
        visiting.remove(identity)
        visited.add(identity)
    for identity in rows:
        visit(identity)
    assert all(rows[d]['complete'] for d in rows['3.9']['dependencies'])
    assert not rows['3.10']['complete'], '3.10 must retain its separate acceptance'
    print(json.dumps({'total': len(rows), 'completed': sum(r['complete'] for r in rows.values()),
                      'task_3_9': rows['3.9'], 'task_3_10': rows['3.10'],
                      'unknown_dependencies': [], 'cycles': []}, ensure_ascii=False))


if __name__ == '__main__':
    main()
