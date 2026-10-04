"""Trie (prefix tree) for autocomplete."""

import heapq

DEFAULT_SUGGESTIONS = 5


class _Node:
    """One letter position in the trie."""

    __slots__ = ("children", "is_word", "weight")

    def __init__(self) -> None:
        """Create a node with no children and no word ending here."""
        self.children: dict[str, _Node] = {}
        self.is_word = False
        self.weight = 0


class Trie:
    """A tree where each path from the root spells a prefix.

    Words that share a beginning share the same path. For the words
    ``lab``, ``laplace`` and ``lecture`` the tree looks like this::

        (root)
          └─ l
              ├─ a
              │   ├─ b          <- word "lab" ends here
              │   └─ p ─ l ─ a ─ c ─ e   <- word "laplace" ends here
              └─ e ─ c ─ t ─ u ─ r ─ e   <- word "lecture" ends here

    Finding every word starting with ``"la"`` means walking two letters down,
    then collecting the words below that node. The walk costs O(prefix length);
    collecting costs O(size of the subtree below the prefix). Neither depends on
    how many words are stored elsewhere in the trie.
    """

    def __init__(self) -> None:
        """Create an empty trie."""
        self._root = _Node()
        self._word_count = 0

    def insert(self, word: str, weight: int = 1) -> None:
        """Add a word, or add weight to a word already stored.

        Inserting a word that already exists *adds* ``weight`` to its stored
        weight and does not change the number of distinct words.

        Args:
            word: The word to add.
            weight: How popular the word is; used to order suggestions.

        Raises:
            ValueError: If ``word`` is empty.
        """
        if not word:
            raise ValueError("Cannot insert an empty word")
        node = self._root
        for letter in word:
            node = node.children.setdefault(letter, _Node())
        if not node.is_word:
            node.is_word = True
            self._word_count += 1
        node.weight += weight

    def __len__(self) -> int:
        """Return the number of distinct words stored."""
        return self._word_count

    def __contains__(self, word: object) -> bool:
        """Return whether ``word`` was inserted (a mere prefix does not count)."""
        if not isinstance(word, str) or not word:
            return False
        node = self._find(word)
        return node is not None and node.is_word

    def complete(self, prefix: str, limit: int = DEFAULT_SUGGESTIONS) -> list[str]:
        """Return stored words starting with ``prefix``.

        Behaviour:

        * Heaviest words first; equal weights in alphabetical order.
        * The prefix itself is included if it is a stored word.
        * An empty prefix matches every word.
        * An unknown prefix, or ``limit <= 0``, gives an empty list.

        Method: walk down to the prefix's node, then run a depth-first search
        below it (with an explicit stack, so deep tries cannot hit Python's
        recursion limit), collecting ``(weight, word)`` wherever a word ends.
        The best ``limit`` are chosen with a heap, O(m log limit) for m matches.

        Args:
            prefix: Beginning of a word.
            limit: Maximum number of suggestions.

        Returns:
            Up to ``limit`` words.
        """
        if limit <= 0:
            return []
        start = self._find(prefix)
        if start is None:
            return []

        found: list[tuple[int, str]] = []
        stack: list[tuple[_Node, str]] = [(start, prefix)]
        while stack:
            node, text = stack.pop()
            if node.is_word:
                found.append((node.weight, text))
            for letter, child in node.children.items():
                stack.append((child, text + letter))

        best = heapq.nsmallest(limit, found, key=lambda item: (-item[0], item[1]))
        return [word for _, word in best]

    def _find(self, prefix: str) -> _Node | None:
        """Walk down the tree along ``prefix`` and return the node it ends at.

        Args:
            prefix: Letters to follow from the root.

        Returns:
            The node reached, or ``None`` if the path does not exist.
        """
        node = self._root
        for letter in prefix:
            child = node.children.get(letter)
            if child is None:
                return None
            node = child
        return node
