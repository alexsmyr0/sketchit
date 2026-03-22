from django.db import models


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class WordPack(TimestampedModel):
    name = models.CharField(max_length=255)
    words = models.ManyToManyField(
        "Word",
        through="WordPackEntry",
        related_name="packs",
    )

    class Meta:
        ordering = ("name", "id")
        db_table = "word_lists"

    def __str__(self):
        return self.name


class Word(TimestampedModel):
    text = models.CharField(max_length=255)

    class Meta:
        ordering = ("text", "id")
        db_table = "words"

    def __str__(self):
        return self.text


class WordPackEntry(TimestampedModel):
    word_pack = models.ForeignKey(
        WordPack,
        on_delete=models.CASCADE,
        related_name="word_pack_entries",
        db_column="word_list_id",
    )
    word = models.ForeignKey(
        Word,
        on_delete=models.CASCADE,
        related_name="word_pack_entries",
        db_column="word_id",
    )

    class Meta:
        ordering = ("word_pack_id", "word_id", "id")
        db_table = "word_list_entries"
        constraints = [
            models.UniqueConstraint(
                fields=("word_pack", "word"),
                name="uq_word_list_entries_word_list_word",
            )
        ]
