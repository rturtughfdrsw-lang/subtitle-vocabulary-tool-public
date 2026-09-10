class MeaningForEveryWordDictionary:
    def lookup_many(self, words):
        return {word: f"测试释义：{word}" for word in words}


class KnownFrequency:
    source = "wordfreq"
    version = "3.1.1"

    def lookup_many(self, words):
        return {word: 3.0 for word in words}


def import_without_enrichment(
    import_vocabulary, task_folder, database_path, *, collection_id=None
):
    return import_vocabulary(
        task_folder,
        database_path,
        dictionary_provider=MeaningForEveryWordDictionary(),
        frequency_provider=KnownFrequency(),
        collection_id=collection_id,
    )
