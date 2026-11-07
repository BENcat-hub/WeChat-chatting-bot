from text.symbols import *

_symbol_to_id = {s: i for i, s in enumerate(symbols)}


def cleaned_text_to_sequence(cleaned_text, tones, language):
    """Converts a string of text to a sequence of IDs corresponding to the symbols in the text."""
    phones = [_symbol_to_id[symbol] for symbol in cleaned_text]
    tone_start = language_tone_start_map[language]
    tones = [i + tone_start for i in tones]
    lang_id = language_id_map[language]
    lang_ids = [lang_id for i in phones]
    return phones, tones, lang_ids


def get_bert(norm_text, word2ph, language, device):
    # 只按需导入：日文 BERT 依赖 pyopenjtalk / 额外的 japanese 模型目录，
    # 中文/英文路径不应该因为它们的缺失而失败。
    if language == "ZH":
        from .chinese_bert import get_bert_feature as fn
    elif language == "EN":
        from .english_bert_mock import get_bert_feature as fn
    else:
        from .japanese_bert import get_bert_feature as fn
    return fn(norm_text, word2ph, device)
