"""Commit-pinned C# syntax chunks with local full-text retrieval; no model calls."""
import hashlib
import contextlib
import json
import re
import sqlite3
from pathlib import Path

from tree_sitter import Language, Parser
import tree_sitter_c_sharp

FORMAT = 'csharp-structure-v1'
DECLARATIONS = {'method_declaration', 'constructor_declaration', 'property_declaration',
                'field_declaration', 'enum_declaration', 'delegate_declaration',
                'operator_declaration', 'conversion_operator_declaration'}
TYPES = {'class_declaration', 'struct_declaration', 'interface_declaration', 'record_declaration'}


def words(text):
    original = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]{2,}', text)
    text = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', text)
    return re.findall(r'[a-z0-9_]{2,}|[\u3400-\u9fff]', text.lower()) + [s.lower() for s in original if re.search('[a-z][A-Z]',s)]


class CodeIndex:
    def __init__(self, sha):
        self.sha = sha
        self.db = sqlite3.connect(':memory:', check_same_thread=False)
        self.db.executescript('''
          CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
          CREATE TABLE files (path TEXT PRIMARY KEY, digest TEXT);
          CREATE VIRTUAL TABLE chunks USING fts5(path UNINDEXED, start UNINDEXED,
              end UNINDEXED, symbol UNINDEXED, body UNINDEXED, title_terms, body_terms);
        ''')
        self.db.executemany('INSERT INTO meta VALUES (?,?)', [('sha', sha), ('format', FORMAT)])
        self.db.commit()
        self.parser = Parser(Language(tree_sitter_c_sharp.language()))

    def add(self, path, body):
        if len(body.encode()) > 2_000_000 or not path.endswith(('.cs', '.md')):
            return
        digest = hashlib.sha256(body.encode()).hexdigest()
        old = self.db.execute('SELECT digest FROM files WHERE path=?', (path,)).fetchone()
        if old and old[0] == digest:
            return
        lines = body.splitlines()
        spans = []
        if path.endswith('.cs'):
            raw = body.encode()
            tree = self.parser.parse(raw)
            stack = [(tree.root_node, '')]
            while stack:
                node, owner = stack.pop()
                name_node = node.child_by_field_name('name')
                name = raw[name_node.start_byte:name_node.end_byte].decode() if name_node else ''
                symbol = '.'.join(filter(None, (owner, name)))
                if node.type in TYPES:
                    block = node.child_by_field_name('body')
                    end = block.start_point.row if block else node.end_point.row
                    spans.append((node.start_point.row, end + 1, symbol))
                    owner = symbol
                elif node.type in DECLARATIONS:
                    spans.append((node.start_point.row, node.end_point.row + 1, symbol or owner))
                    continue
                stack.extend((child, owner) for child in reversed(node.named_children))
        if not spans:
            spans = [(0, len(lines), Path(path).stem)]
        rows = []
        for start, end, symbol in spans:
            for first in range(start, end, 70):
                last = min(end, first + 80)
                snippet = '\n'.join(lines[first:last])[:6000]
                rows.append((path, first+1, last, symbol, snippet,
                             ' '.join(words(path+' '+symbol)), ' '.join(words(snippet))))
        with self.db:
            self.db.execute('DELETE FROM chunks WHERE path=?', (path,))
            self.db.execute('INSERT OR REPLACE INTO files VALUES (?,?)', (path, digest))
            self.db.executemany('INSERT INTO chunks VALUES (?,?,?,?,?,?,?)', rows)

    def search(self, query, limit=5, budget=10000):
        terms = list(dict.fromkeys(words(query)))[:24]
        if not terms:
            return []
        expression = ' OR '.join('"'+word+'"' for word in terms)
        rows = self.db.execute('''SELECT path,start,end,symbol,body
            FROM chunks WHERE chunks MATCH ? ORDER BY bm25(chunks,0,0,0,0,0,5,1) LIMIT 40''',
                               (expression,)).fetchall()
        result = []
        seen = set()
        for path, start, end, symbol, body in rows:
            if (path, symbol) in seen:
                continue
            record = {'path':path, 'start':int(start), 'end':int(end), 'symbol':symbol,
                      'text':body, 'sha':self.sha}
            size = len(json.dumps(record, ensure_ascii=False).encode())
            if size > budget:
                continue
            budget -= size
            result.append(record)
            seen.add((path, symbol))
            if len(result) >= limit:
                break
        return result

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        with contextlib.closing(sqlite3.connect(temporary)) as target:
            self.db.backup(target)
        temporary.replace(path)

    @classmethod
    def restore(cls, path, sha):
        source = sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=ro', uri=True)
        try:
            meta = dict(source.execute('SELECT key,value FROM meta'))
            if meta != {'sha':sha, 'format':FORMAT}:
                raise ValueError('Code index version mismatch')
            result = cls(sha)
            source.backup(result.db)
            return result
        finally:
            source.close()
