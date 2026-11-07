from text import chinese, cleaned_text_to_sequence

# 日文模块依赖 pyopenjtalk（要 cmake 编译），本机只跑中文，改成按需导入。
_JP = None


def _jp():
    global _JP
    if _JP is None:
        from text import japanese as _m

        _JP = _m
    return _JP


language_module_map = {"ZH": chinese}


def _module(language):
    if language == "JP":
        return _jp()
    return language_module_map[language]


def clean_text(text, language):
    language_module = _module(language)
    norm_text = language_module.text_normalize(text)
    phones, tones, word2ph = language_module.g2p(norm_text)
    return norm_text, phones, tones, word2ph


def clean_text_bert(text, language):
    language_module = _module(language)
    norm_text = language_module.text_normalize(text)
    phones, tones, word2ph = language_module.g2p(norm_text)
    bert = language_module.get_bert_feature(norm_text, word2ph)
    return phones, tones, bert


def text_to_sequence(text, language):
    norm_text, phones, tones, word2ph = clean_text(text, language)
    return cleaned_text_to_sequence(phones, tones, language)


if __name__ == "__main__":
    pass
