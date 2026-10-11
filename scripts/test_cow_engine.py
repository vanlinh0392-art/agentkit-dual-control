# Test Suite for AgentKit CoW Engine v1.0
import os, shutil, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from cow_engine import CoWSnapshotEngine

def run_test():
    test_root = Path(tempfile.mkdtemp(prefix='agentkit_cow_test_'))
    print(f'Test Root: {test_root}')
    try:
        master = test_root / 'master'
        master.mkdir(parents=True)
        (master / 'src').mkdir()
        (master / 'src' / 'main.py').write_text('print("original")', encoding='utf-8')
        (master / 'README.md').write_text('# Master', encoding='utf-8')
        heavy = master / 'node_modules' / 'pkg'
        heavy.mkdir(parents=True)
        (heavy / 'index.js').write_text('12345', encoding='utf-8')
        
        ws1 = test_root / 'ws1'
        ws2 = test_root / 'ws2'
        res1 = CoWSnapshotEngine.create_snapshot(master, ws1)
        res2 = CoWSnapshotEngine.create_snapshot(master, ws2)
        assert res1['success'] and res2['success']
        print(f'Created ws1 in {res1["elapsed_ms"]}ms, ws2 in {res2["elapsed_ms"]}ms')
        
        CoWSnapshotEngine.safe_write_file(ws1 / 'src' / 'main.py', 'print("modified by agent 1")')
        (ws1 / 'src' / 'new.py').write_text('new', encoding='utf-8')
        
        assert (master / 'src' / 'main.py').read_text(encoding='utf-8') == 'print("original")'
        assert (ws2 / 'src' / 'main.py').read_text(encoding='utf-8') == 'print("original")'
        print('Break-on-write Isolation Passed!')
        
        diff1 = CoWSnapshotEngine.extract_diff(ws1)
        print('Diff Scan:', diff1)
        assert diff1['modified_count'] == 1 and diff1['added_count'] == 1
        
        stats1 = CoWSnapshotEngine.get_stats(ws1, master)
        print('Stats:', stats1)
        
        CoWSnapshotEngine.prune_snapshot(ws1)
        CoWSnapshotEngine.prune_snapshot(ws2)
        assert not ws1.exists() and not ws2.exists()
        assert (master / 'node_modules' / 'pkg' / 'index.js').exists()
        print('Prune Passed!')
        print('ALL TESTS SUCCESSFUL!')
    finally:
        shutil.rmtree(test_root, ignore_errors=True)

if __name__ == '__main__':
    run_test()
