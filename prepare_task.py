"""Prepare a real benchmark through the same QA lookup used by Discord; never dispatch."""
import argparse
import asyncio
import json
from pathlib import Path
import re
import time
import aiohttp
from fa_bot.config import secret
from fa_bot.github import GitHub
from fa_bot.handoff import encode, ready
from fa_bot.knowledge import Knowledge
from fa_bot.qa import answer
from fa_bot.store import Store


async def prepare(session, key, knowledge, request):
    reply, cost = await answer(session, key, knowledge, request)
    accepted = bool(reply.change_request) and ready(reply.localization)
    return {'accepted': accepted, 'qa_cost': cost,
            'answer': reply.text, 'source_commit': knowledge.sha,
            'request': encode(reply.change_request, reply.localization) if accepted else None}


async def main(args):
    if not re.fullmatch('[a-f0-9]{40}', args.source_commit):
        raise ValueError('Use a full pinned source commit')
    import os
    store = Store(os.environ['STATE_FILE'])
    key = 'qa-benchmark-'+str(time.time_ns())
    try:
        async with aiohttp.ClientSession() as session:
            knowledge = Knowledge()
            await knowledge.refresh(GitHub(session, secret('GITHUB_TOKEN'), ''), args.source_commit)
            store.reserve(key, 'qa', .02)
            result = await prepare(session, secret('OPENAI_API_KEY'), knowledge,
                                   Path(args.request_file).read_text(encoding='utf-8-sig'))
            store.settle(key, result['qa_cost'])
            Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'accepted':result['accepted'], 'qa_cost':result['qa_cost']}))
    finally:
        store.db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--request-file', required=True)
    parser.add_argument('--output', required=True)
    asyncio.run(main(parser.parse_args()))
