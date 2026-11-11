"""模型接口：不绑定哪一家。现在会说四种格式，都用流式：
    anthropic   Anthropic Messages            <地址>/messages
    openai      OpenAI 兼容 chat completions   <地址>/chat/completions
    responses   OpenAI Responses              <地址>/responses
    gemini      Google Gemini                 <地址>/models/<模型>:streamGenerateContent
要再加一种格式：写一个"把历史变成对方的写法 + 读它的流"的函数，登记进文件末尾的 FORMATS。

对话历史用中性的写法保存（见 loop.py）：
    {'role': 'user', 'text': 文字, 'images': [图片路径]}
    {'role': 'assistant', 'parts': [{'type': 'text'|'thinking', 'text': …}, {'type': 'tool', 'id', 'name', 'input'}], 'raw': 原样, 'by': 档案/模型}
    {'role': 'tool', 'results': [{'id', 'name', 'text', 'error'}]}
每次调用时在这里变成对方要的格式。同一个模型自己说过的话原样放回（思考块不能改）；换了模型就只带文字和工具调用。
"""
import base64
import json
import mimetypes
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import config

# 每百万 token 的官方标价（输入，输出），美元。不在这里、档案里也没填的，价格显示"未知"。
PRICES = {'claude-fable-5-1': (10, 50), 'claude-opus-5-5': (4, 20), 'claude-sonnet-5-5': (2, 10),
          'claude-haiku-5-5': (0.10, 0.50)}
EFFORTS = ['', 'low', 'medium', 'high', 'xhigh', 'max']
_clients = {}


class ModelError(Exception):
    """模型服务那边的错，原话给用户看。transient=True 表示是临时的（对方忙、断线），可以等一下自动再试。"""
    transient = False


def _client(profile):
    local = (urlparse(profile['base_url']).hostname or '') in ('127.0.0.1', 'localhost', '::1')
    if local not in _clients:               # 本机的服务不走系统代理，外面的走
        _clients[local] = httpx.Client(trust_env=not local, timeout=httpx.Timeout(600, connect=20))
    return _clients[local]


def _headers(profile):
    key = config.key_for(profile['id']) if profile.get('auth') == 'key' else ''
    if profile['format'] == 'anthropic':
        return {'anthropic-version': '2023-06-01', **({'x-api-key': key} if key else {})}
    if profile['format'] == 'gemini':
        return {'x-goog-api-key': key} if key else {}
    return {'Authorization': f'Bearer {key}'} if key else {}


def _url(profile, tail):
    return profile['base_url'].rstrip('/') + tail


def models(profile):
    """问服务有哪些模型。问不到时只给手填的，并说明原因。"""
    found, problem = [], ''
    try:
        response = _client(profile).get(_url(profile, '/models'), headers=_headers(profile), timeout=20)
        if response.status_code >= 400:
            problem = f'{response.status_code}：{response.text[:200]}'
        else:
            data = response.json()          # OpenAI / Anthropic 给 data[].id，Gemini 给 models[].name（带 models/ 前缀）
            found = sorted(filter(None, ((item.get('id') or item.get('name') or '').removeprefix('models/')
                                         for item in data.get('data') or data.get('models') or [])))
    except (httpx.HTTPError, ValueError, AttributeError, TypeError) as error:
        problem = f'{type(error).__name__}: {error}'
    typed = [name for name in [profile.get('model'), *profile.get('extra_models', [])] if name]
    return {'models': list(dict.fromkeys(typed + found)), 'discovered': len(found), 'problem': problem}


def price(profile, model):
    """(输入价, 输出价) 每百万 token；不知道就是 None。"""
    if profile.get('price_in') not in (None, '') and profile.get('price_out') not in (None, ''):
        return float(profile['price_in']), float(profile['price_out'])
    return next((value for name, value in PRICES.items() if name in (model or '')), None)


def cost(usage, rate):
    if not rate:
        return None
    return ((usage.get('input', 0) + 1.25 * usage.get('cache_write', 0) + 0.1 * usage.get('cache_read', 0)) * rate[0]
            + usage.get('output', 0) * rate[1]) / 1e6


def _image(path):
    kind = mimetypes.guess_type(str(path))[0] or 'image/png'
    return kind, base64.b64encode(Path(path).read_bytes()).decode('ascii')


def _sse(response):
    data = []
    for line in response.iter_lines():
        if line.startswith('data:'):
            data.append(line[5:].lstrip())
        elif not line and data:
            yield '\n'.join(data)
            data = []
    if data:
        yield '\n'.join(data)


def _open(profile, tail, body):
    return _client(profile).stream('POST', _url(profile, tail), json=body, headers=_headers(profile))


def _check(response, profile):
    if response.status_code >= 400:
        text = response.read().decode('utf-8', 'replace')
        try:                                # 能读出对方的原话就给原话，读不出就给原文
            said = json.loads(text).get('error')
            text = (said.get('message') if isinstance(said, dict) else said) or text
        except (ValueError, AttributeError):
            pass
        hint = '（检查设置里的密钥）' if response.status_code in (401, 403) else ''
        error = ModelError(f'模型服务返回 {response.status_code}{hint}：{text[:1500]}')
        error.transient = response.status_code in (408, 409, 425, 429, 500, 502, 503, 504, 529)
        raise error


def _arguments(text):
    """模型写的工具参数。没写完（输出到了上限）时给出 None。"""
    try:
        value = json.loads(text or '{}')
        return value if isinstance(value, dict) else None
    except ValueError:
        return None


# ---------- Anthropic Messages ----------

def _anthropic_messages(messages, by):
    out = []
    for message in messages:
        if message['role'] == 'user':
            content = [{'type': 'image', 'source': {'type': 'base64', 'media_type': kind, 'data': data}}
                       for kind, data in map(_image, message.get('images') or [])]
            out.append({'role': 'user', 'content': content + [{'type': 'text', 'text': message['text'] or '（空）'}]})
        elif message['role'] == 'assistant':
            raw = message.get('raw') or {}
            if raw.get('format') == 'anthropic' and message.get('by') == by:
                content = raw['content']
            else:
                content = [{'type': 'text', 'text': part['text']} if part['type'] == 'text' else
                           {'type': 'tool_use', 'id': part['id'], 'name': part['name'], 'input': part['input'] or {}}
                           for part in message['parts'] if part['type'] == 'tool' or (part['type'] == 'text' and part['text'].strip())]
            if content:
                out.append({'role': 'assistant', 'content': content})
        elif message['role'] == 'tool':
            def result_content(item):
                pictures = [{'type': 'image', 'source': {'type': 'base64', 'media_type': kind, 'data': data}}
                            for kind, data in map(_image, item.get('images') or [])]
                return [{'type': 'text', 'text': item['text'] or '（空）'}] + pictures if pictures else item['text'] or '（空）'
            out.append({'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': item['id'], 'content': result_content(item),
                 **({'is_error': True} if item.get('error') else {})} for item in message['results']]})
    if out:                                 # 缓存点跟着最后一条消息走
        last = out[-1]
        last['content'] = last['content'][:-1] + [{**last['content'][-1], 'cache_control': {'type': 'ephemeral'}}]
    return out


def _stream_anthropic(profile, model, system, tools, messages, emit, stop):
    by = f'{profile["id"]}/{model}'
    body = {'model': model, 'max_tokens': int(profile.get('max_output') or 16000), 'stream': True,
            'system': [{'type': 'text', 'text': system, 'cache_control': {'type': 'ephemeral'}}],
            'messages': _anthropic_messages(messages, by)}
    if tools:
        body['tools'] = tools
    if profile.get('thinking'):
        body['thinking'] = {'type': 'adaptive'}
    if profile.get('effort'):
        body['output_config'] = {'effort': profile['effort']}
    blocks, usage, reason, stopped = {}, {}, None, False
    with _open(profile, '/messages', body) as response:
        _check(response, profile)
        for data in _sse(response):
            if stop.is_set():
                stopped = True
                break
            event = json.loads(data)
            kind = event.get('type')
            if kind == 'message_start':
                usage.update({key: value for key, value in (event['message'].get('usage') or {}).items() if isinstance(value, int)})
            elif kind == 'content_block_start':
                block = dict(event['content_block'])
                blocks[event['index']] = block
                if block['type'] == 'tool_use':
                    block['_json'] = ''
                    emit('tool_start', {'id': block['id'], 'name': block['name']})
            elif kind == 'content_block_delta':
                block, delta = blocks[event['index']], event['delta']
                if delta['type'] == 'text_delta':
                    block['text'] = block.get('text', '') + delta['text']
                    emit('text', delta['text'])
                elif delta['type'] == 'thinking_delta':
                    block['thinking'] = block.get('thinking', '') + delta['thinking']
                    emit('thinking', delta['thinking'])
                elif delta['type'] == 'signature_delta':
                    block['signature'] = block.get('signature', '') + delta['signature']
                elif delta['type'] == 'input_json_delta':
                    block['_json'] += delta['partial_json']
            elif kind == 'content_block_stop':
                block = blocks[event['index']]
                if block['type'] == 'tool_use':
                    block['input'] = _arguments(block.pop('_json'))
            elif kind == 'message_delta':
                reason = event['delta'].get('stop_reason') or reason
                usage.update({key: value for key, value in (event.get('usage') or {}).items() if isinstance(value, int)})
            elif kind == 'error':
                raise ModelError(f'模型服务中途出错：{json.dumps(event.get("error"), ensure_ascii=False)}')
    content = [block for _, block in sorted(blocks.items()) if '_json' not in block]      # 没写完的工具调用不要
    parts = []
    for block in content:
        if block['type'] == 'text' and block.get('text'):
            parts.append({'type': 'text', 'text': block['text']})
        elif block['type'] == 'thinking' and block.get('thinking'):
            parts.append({'type': 'thinking', 'text': block['thinking']})
        elif block['type'] == 'tool_use':
            parts.append({'type': 'tool', 'id': block['id'], 'name': block['name'], 'input': block['input']})
    for block in content:                   # 参数坏了的工具调用：历史里放空参数，工具那边会回一条错误让模型重写
        if block['type'] == 'tool_use' and block['input'] is None:
            block['input'] = {}
    final = {'end_turn': 'end', 'stop_sequence': 'end', 'tool_use': 'tool', 'max_tokens': 'max', 'pause_turn': 'pause',
             'refusal': 'refusal'}.get(reason, 'end')
    return {'parts': parts, 'raw': None if stopped else {'format': 'anthropic', 'content': content},
            'stop': 'stopped' if stopped else final,
            'usage': {'input': usage.get('input_tokens', 0), 'output': usage.get('output_tokens', 0),
                      'cache_read': usage.get('cache_read_input_tokens', 0),
                      'cache_write': usage.get('cache_creation_input_tokens', 0)}}


# ---------- OpenAI 兼容 ----------

def _openai_messages(system, messages, by):
    out = [{'role': 'system', 'content': system}]
    for message in messages:
        if message['role'] == 'user':
            images = [{'type': 'image_url', 'image_url': {'url': f'data:{kind};base64,{data}'}}
                      for kind, data in map(_image, message.get('images') or [])]
            out.append({'role': 'user', 'content': [{'type': 'text', 'text': message['text']}] + images if images
                        else message['text'] or '（空）'})
        elif message['role'] == 'assistant':
            raw = message.get('raw') or {}
            if raw.get('format') == 'openai' and message.get('by') == by:
                out.append(raw['message'])
                continue
            text = '\n'.join(part['text'] for part in message['parts'] if part['type'] == 'text')
            calls = [{'id': part['id'], 'type': 'function',
                      'function': {'name': part['name'], 'arguments': json.dumps(part['input'] or {}, ensure_ascii=False)}}
                     for part in message['parts'] if part['type'] == 'tool']
            if text.strip() or calls:
                out.append({'role': 'assistant', 'content': text or None, **({'tool_calls': calls} if calls else {})})
        elif message['role'] == 'tool':
            out += [{'role': 'tool', 'tool_call_id': item['id'], 'content': item['text'] or '（空）'} for item in message['results']]
            pictures = _tool_images(message)     # 工具结果里不能放图：紧跟一条用户消息带图
            if pictures:
                out.append({'role': 'user', 'content': [{'type': 'text', 'text': '[look 工具取来的图]'}] +
                            [{'type': 'image_url', 'image_url': {'url': f'data:{kind};base64,{data}'}} for kind, data in pictures]})
    return out


def _tool_images(message):
    return [_image(path) for item in message['results'] for path in item.get('images') or []]


def _stream_openai(profile, model, system, tools, messages, emit, stop):
    by = f'{profile["id"]}/{model}'
    body = {'model': model, 'messages': _openai_messages(system, messages, by), 'stream': True,
            'stream_options': {'include_usage': True}, 'max_tokens': int(profile.get('max_output') or 16000)}
    if tools:
        body['tools'] = [{'type': 'function', 'function': {'name': item['name'], 'description': item['description'],
                                                           'parameters': item['input_schema']}} for item in tools]
    if profile.get('effort'):
        body['reasoning_effort'] = profile['effort']
    text, thinking, calls, usage, reason, stopped = '', '', {}, {}, None, False
    with _open(profile, '/chat/completions', body) as response:
        _check(response, profile)
        for data in _sse(response):
            if stop.is_set():
                stopped = True
                break
            if data.strip() == '[DONE]':
                break
            chunk = json.loads(data)
            if chunk.get('error'):
                raise ModelError(f'模型服务中途出错：{json.dumps(chunk["error"], ensure_ascii=False)}')
            usage = chunk.get('usage') or usage
            for choice in chunk.get('choices') or []:
                delta = choice.get('delta') or {}
                thought = delta.get('reasoning_content') or delta.get('reasoning')
                if isinstance(thought, str) and thought:
                    thinking += thought
                    emit('thinking', thought)
                if delta.get('content'):
                    text += delta['content']
                    emit('text', delta['content'])
                for call in delta.get('tool_calls') or []:
                    slot = calls.setdefault(call.get('index', 0), {'id': '', 'name': '', 'arguments': ''})
                    function = call.get('function') or {}
                    slot['id'] = slot['id'] or call.get('id') or ''
                    if function.get('name') and not slot['name']:
                        slot['name'] = function['name']
                        emit('tool_start', {'id': slot['id'], 'name': slot['name']})
                    slot['arguments'] += function.get('arguments') or ''
                reason = choice.get('finish_reason') or reason
    slots = [] if stopped else [slot for _, slot in sorted(calls.items()) if slot['name']]
    for slot in slots:
        slot['id'] = slot['id'] or f'call_{uuid.uuid4().hex[:12]}'
    parts = ([{'type': 'thinking', 'text': thinking}] if thinking else []) + ([{'type': 'text', 'text': text}] if text else [])
    parts += [{'type': 'tool', 'id': slot['id'], 'name': slot['name'], 'input': _arguments(slot['arguments'])} for slot in slots]
    message = {'role': 'assistant', 'content': text or None}
    if slots:
        message['tool_calls'] = [{'id': slot['id'], 'type': 'function', 'function': {
            'name': slot['name'], 'arguments': slot['arguments'] if _arguments(slot['arguments']) is not None else '{}'}}
            for slot in slots]
    if thinking:
        message['reasoning_content'] = thinking
    details = usage.get('prompt_tokens_details') or {}
    read, write = details.get('cached_tokens') or 0, details.get('cache_write_tokens') or 0
    final = 'tool' if slots else {'length': 'max', 'content_filter': 'refusal'}.get(reason, 'end')
    return {'parts': parts, 'raw': None if stopped else {'format': 'openai', 'message': message},
            'stop': 'stopped' if stopped else final,
            'usage': {'input': max((usage.get('prompt_tokens') or 0) - read - write, 0),
                      'output': usage.get('completion_tokens') or 0, 'cache_read': read, 'cache_write': write}}


# ---------- OpenAI Responses ----------

def _responses_input(messages, by):
    out = []
    for message in messages:
        if message['role'] == 'user':
            images = [{'type': 'input_image', 'image_url': f'data:{kind};base64,{data}'}
                      for kind, data in map(_image, message.get('images') or [])]
            out.append({'role': 'user', 'content': [{'type': 'input_text', 'text': message['text'] or '（空）'}] + images})
        elif message['role'] == 'assistant':
            raw = message.get('raw') or {}
            if raw.get('format') == 'responses' and message.get('by') == by:
                out += raw['output']        # 原样的输出项（含加密的推理项）
                continue
            for part in message['parts']:
                if part['type'] == 'text' and part['text'].strip():
                    out.append({'role': 'assistant', 'content': [{'type': 'output_text', 'text': part['text']}]})
                elif part['type'] == 'tool':
                    out.append({'type': 'function_call', 'call_id': part['id'], 'name': part['name'],
                                'arguments': json.dumps(part['input'] or {}, ensure_ascii=False)})
        elif message['role'] == 'tool':
            out += [{'type': 'function_call_output', 'call_id': item['id'], 'output': item['text'] or '（空）'}
                    for item in message['results']]
            pictures = _tool_images(message)
            if pictures:
                out.append({'role': 'user', 'content': [{'type': 'input_text', 'text': '[look 工具取来的图]'}] +
                            [{'type': 'input_image', 'image_url': f'data:{kind};base64,{data}'} for kind, data in pictures]})
    return out


def _stream_responses(profile, model, system, tools, messages, emit, stop):
    by = f'{profile["id"]}/{model}'
    body = {'model': model, 'instructions': system, 'input': _responses_input(messages, by), 'stream': True, 'store': False,
            'max_output_tokens': int(profile.get('max_output') or 16000)}
    if tools:
        body['tools'] = [{'type': 'function', 'name': item['name'], 'description': item['description'],
                          'parameters': item['input_schema']} for item in tools]
    if profile.get('effort'):
        body['reasoning'] = {'effort': profile['effort']}
    if profile.get('thinking'):             # 不存在对方那里，所以把加密的推理要回来，下一轮原样带上
        body['include'] = ['reasoning.encrypted_content']
    items, final, stopped = [], {}, False
    with _open(profile, '/responses', body) as response:
        _check(response, profile)
        for data in _sse(response):
            if stop.is_set():
                stopped = True
                break
            if data.strip() == '[DONE]':
                break
            event = json.loads(data)
            kind = event.get('type') or ''
            if kind == 'response.output_text.delta':
                emit('text', event.get('delta') or '')
            elif kind in ('response.reasoning_summary_text.delta', 'response.reasoning_text.delta'):
                emit('thinking', event.get('delta') or '')
            elif kind == 'response.output_item.added' and (event.get('item') or {}).get('type') == 'function_call':
                emit('tool_start', {'id': event['item'].get('call_id') or '', 'name': event['item'].get('name') or ''})
            elif kind == 'response.output_item.done':
                items.append(event['item'])
            elif kind in ('response.completed', 'response.incomplete'):
                final = event.get('response') or {}
            elif kind in ('response.failed', 'error'):
                problem = (event.get('response') or {}).get('error') or event.get('error') or event
                raise ModelError(f'模型服务中途出错：{json.dumps(problem, ensure_ascii=False)[:800]}')
    parts = []
    for item in items:
        if item.get('type') == 'reasoning':
            thought = '\n'.join(piece.get('text', '') for piece in item.get('summary') or [])
            if thought:
                parts.append({'type': 'thinking', 'text': thought})
        elif item.get('type') == 'message':
            said = ''.join(piece.get('text') or piece.get('refusal') or '' for piece in item.get('content') or [])
            if said:
                parts.append({'type': 'text', 'text': said})
        elif item.get('type') == 'function_call':
            parts.append({'type': 'tool', 'id': item.get('call_id') or f'call_{uuid.uuid4().hex[:12]}', 'name': item.get('name'),
                          'input': _arguments(item.get('arguments'))})
    usage = final.get('usage') or {}
    read = (usage.get('input_tokens_details') or {}).get('cached_tokens') or 0
    calls = any(part['type'] == 'tool' for part in parts)
    reason = (final.get('incomplete_details') or {}).get('reason')
    return {'parts': parts, 'raw': None if stopped else {'format': 'responses', 'output': [
                {key: value for key, value in item.items() if key != 'status'} for item in items]},
            'stop': 'stopped' if stopped else 'tool' if calls else 'max' if reason == 'max_output_tokens' else
                    'refusal' if reason == 'content_filter' else 'end',
            'usage': {'input': max((usage.get('input_tokens') or 0) - read, 0), 'output': usage.get('output_tokens') or 0,
                      'cache_read': read, 'cache_write': 0}}


# ---------- Google Gemini ----------

def _gemini_contents(messages, by):
    out = []
    for message in messages:
        if message['role'] == 'user':
            images = [{'inlineData': {'mimeType': kind, 'data': data}} for kind, data in map(_image, message.get('images') or [])]
            out.append({'role': 'user', 'parts': images + [{'text': message['text'] or '（空）'}]})
        elif message['role'] == 'assistant':
            raw = message.get('raw') or {}
            if raw.get('format') == 'gemini' and message.get('by') == by:
                parts = raw['parts']        # 原样（带 thoughtSignature）
            else:
                parts = [{'text': part['text']} if part['type'] == 'text' else
                         {'functionCall': {'name': part['name'], 'args': part['input'] or {}}}
                         for part in message['parts'] if part['type'] == 'tool' or (part['type'] == 'text' and part['text'].strip())]
            if parts:
                out.append({'role': 'model', 'parts': parts})
        elif message['role'] == 'tool':
            out.append({'role': 'user', 'parts': [{'functionResponse': {'name': item['name'], 'response': {'result': item['text']}}}
                                                  for item in message['results']] +
                        [{'inlineData': {'mimeType': kind, 'data': data}} for kind, data in _tool_images(message)]})
    return out


def _stream_gemini(profile, model, system, tools, messages, emit, stop):
    by = f'{profile["id"]}/{model}'
    body = {'systemInstruction': {'parts': [{'text': system}]}, 'contents': _gemini_contents(messages, by),
            'generationConfig': {'maxOutputTokens': int(profile.get('max_output') or 16000)}}
    if tools:
        body['tools'] = [{'functionDeclarations': [{'name': item['name'], 'description': item['description'],
                                                    'parametersJsonSchema': item['input_schema']} for item in tools]}]
    if profile.get('thinking'):
        body['generationConfig']['thinkingConfig'] = {'includeThoughts': True}
    received, usage, reason, stopped = [], {}, None, False
    with _open(profile, f'/models/{model.removeprefix("models/")}:streamGenerateContent?alt=sse', body) as response:
        _check(response, profile)
        for data in _sse(response):
            if stop.is_set():
                stopped = True
                break
            chunk = json.loads(data)
            if chunk.get('error'):
                raise ModelError(f'模型服务中途出错：{json.dumps(chunk["error"], ensure_ascii=False)[:800]}')
            usage = chunk.get('usageMetadata') or usage
            for candidate in chunk.get('candidates') or []:
                reason = candidate.get('finishReason') or reason
                for part in (candidate.get('content') or {}).get('parts') or []:
                    plain = set(part) <= {'text', 'thought'}            # 没有签名的纯文字块，和前一块并在一起存
                    if plain and received and set(received[-1]) == set(part) and received[-1].get('thought') == part.get('thought'):
                        received[-1] = {**received[-1], 'text': received[-1]['text'] + part['text']}
                    else:
                        received.append(dict(part))
                    if part.get('functionCall'):
                        emit('tool_start', {'id': '', 'name': part['functionCall'].get('name') or ''})
                    elif part.get('text'):
                        emit('thinking' if part.get('thought') else 'text', part['text'])
    parts = []
    for part in received:
        if part.get('functionCall'):
            call = part['functionCall']
            parts.append({'type': 'tool', 'id': call.get('id') or f'call_{uuid.uuid4().hex[:12]}', 'name': call.get('name'),
                          'input': call.get('args') if isinstance(call.get('args'), dict) else {}})
        elif part.get('text'):
            kind = 'thinking' if part.get('thought') else 'text'
            if parts and parts[-1]['type'] == kind:
                parts[-1]['text'] += part['text']
            else:
                parts.append({'type': kind, 'text': part['text']})
    calls = any(part['type'] == 'tool' for part in parts)
    read = usage.get('cachedContentTokenCount') or 0
    return {'parts': parts, 'raw': None if stopped else {'format': 'gemini', 'parts': received},
            'stop': 'stopped' if stopped else 'tool' if calls else 'max' if reason == 'MAX_TOKENS' else
                    'end' if reason in (None, 'STOP') else 'refusal',
            'usage': {'input': max((usage.get('promptTokenCount') or 0) - read, 0), 'cache_read': read, 'cache_write': 0,
                      'output': (usage.get('candidatesTokenCount') or 0) + (usage.get('thoughtsTokenCount') or 0)}}


# 格式名 → (给人看的名字, 调用函数)。设置页的"格式"下拉就是这张表。
FORMATS = {'openai': ('OpenAI 兼容（chat completions）', _stream_openai),
           'anthropic': ('Anthropic（messages）', _stream_anthropic),
           'responses': ('OpenAI Responses', _stream_responses),
           'gemini': ('Google Gemini', _stream_gemini)}


def stream(profile, model, system, tools, messages, emit, stop):
    """调用一次模型。emit(种类, 内容) 收到流出来的字（text、thinking、tool_start）；stop 是 threading.Event，置位就中断。

    返回 {'parts', 'raw', 'stop': end|tool|max|pause|refusal|stopped, 'usage': {input, output, cache_read, cache_write}}。
    """
    if not model:
        raise ModelError('还没有选模型。请在设置里选一个或手填模型名。')
    if profile['format'] not in FORMATS:
        raise ModelError(f'不认识的接口格式：{profile["format"]}。可用：{"、".join(FORMATS)}')
    run = FORMATS[profile['format']][1]
    try:
        return run(profile, model, system, tools, messages, emit, stop)
    except httpx.HTTPError as error:
        problem = ModelError(f'和模型服务 {profile["base_url"]} 的连接出了问题：{type(error).__name__} {error}')
        problem.transient = True
        raise problem
