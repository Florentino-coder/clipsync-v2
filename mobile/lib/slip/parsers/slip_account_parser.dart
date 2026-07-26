import '../slip_ocr.dart';

enum SlipAccountConfidence { high, needsReview }

class SlipAccountParseResult {
  final String? senderAccountToken;
  final String? receiverAccountToken;
  final String? amountToken;
  final SlipAccountConfidence confidence;
  final List<String> accountTokensInOrder;

  const SlipAccountParseResult({
    this.senderAccountToken,
    this.receiverAccountToken,
    this.amountToken,
    required this.confidence,
    this.accountTokensInOrder = const [],
  });
}

final _amountLineRe = RegExp(r'^\d{1,3}(,\d{3})*\.\d{2}$');
final _accountDashRe = RegExp(r'[xX0-9*]{1,4}(-[xX0-9*]{1,7}){1,4}');

String? _amountTokenIn(String text) {
  final compact = text.replaceAll(' ', '');
  final m = RegExp(r'\d{1,3}(,\d{3})*\.\d{2}').firstMatch(compact);
  return m?.group(0);
}

bool _lineIsAmount(String text) {
  final t = _amountTokenIn(text);
  if (t == null) return false;
  return text.replaceAll(' ', '').contains(t);
}

String? _accountTokenIn(String text) {
  final m = _accountDashRe.firstMatch(text);
  if (m != null) {
    final token = m.group(0)!;
    final digits = token.replaceAll(RegExp(r'[^0-9]'), '');
    if (digits.length >= 2 && digits.length < 15) return token;
  }
  // short-mask for stubs like x6789 — only as candidate, never assign roles unless count==2
  final short = RegExp(r'[xX*]{1,6}-?\d{3,4}').firstMatch(text);
  if (short != null) return short.group(0);
  return null;
}

SlipAccountParseResult parseAccountLines(List<OcrLine> lines) {
  final sorted = [...lines]..sort((a, b) => a.yTop.compareTo(b.yTop));

  OcrLine? amountLine;
  for (final line in sorted) {
    if (_lineIsAmount(line.text)) {
      amountLine = line;
      break;
    }
  }
  final amountY = amountLine?.yTop;
  final amountToken =
      amountLine == null ? null : _amountTokenIn(amountLine.text);

  final accounts = <String>[];
  for (final line in sorted) {
    if (amountY != null && line.yTop >= amountY) continue;
    final token = _accountTokenIn(line.text);
    if (token != null) accounts.add(token);
  }

  if (accounts.length == 2) {
    return SlipAccountParseResult(
      senderAccountToken: accounts[0],
      receiverAccountToken: accounts[1],
      amountToken: amountToken,
      confidence: SlipAccountConfidence.high,
      accountTokensInOrder: accounts,
    );
  }
  return SlipAccountParseResult(
    senderAccountToken: null,
    receiverAccountToken: null,
    amountToken: amountToken,
    confidence: SlipAccountConfidence.needsReview,
    accountTokensInOrder: accounts,
  );
}
