class RelaySelector {
  RelaySelector(Iterable<String> urls)
      : _urls = urls
            .map((url) => url.trim())
            .where((url) => url.isNotEmpty)
            .toList();

  final List<String> _urls;
  int _index = 0;

  String get current => _urls.isEmpty ? '' : _urls[_index % _urls.length];

  void connected() {}

  void reset() {
    _index = 0;
  }

  void failed() {
    if (_urls.isNotEmpty) {
      _index = (_index + 1) % _urls.length;
    }
  }
}
