SAFE_TELEGRAM_TEXT_LENGTH = 3800


def chunk_message(text, label, max_length=SAFE_TELEGRAM_TEXT_LENGTH):
    if len(text) <= max_length:
        return [text]
    reserved = len(f"{label} (999/999)\n\n")
    body_limit = max_length - reserved
    blocks = _split_oversized_blocks(text.split("\n\n"), body_limit)
    bodies, current = [], ""
    for block in blocks:
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) <= body_limit:
            current = candidate
        else:
            bodies.append(current)
            current = block
    if current:
        bodies.append(current)
    total = len(bodies)
    return [f"{label} ({index}/{total})\n\n{body}" for index, body in enumerate(bodies, 1)]


def _split_oversized_blocks(blocks, limit):
    result = []
    for block in blocks:
        if len(block) <= limit:
            result.append(block)
            continue
        current = ""
        for line in block.splitlines():
            pieces = [line[index:index + limit] for index in range(0, len(line), limit)] or [""]
            for piece in pieces:
                candidate = piece if not current else f"{current}\n{piece}"
                if len(candidate) <= limit:
                    current = candidate
                else:
                    result.append(current)
                    current = piece
        if current:
            result.append(current)
    return result
