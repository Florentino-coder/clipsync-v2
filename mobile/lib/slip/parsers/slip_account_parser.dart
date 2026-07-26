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

enum _TransferLabel { sender, receiver }

_TransferLabel? _transferLabelIn(String text) {
  if (text.contains('จาก')) return _TransferLabel.sender;
  if (text.contains('ไปยัง')) return _TransferLabel.receiver;
  final lower = text.toLowerCase();
  if (RegExp(r'\bfrom\b').hasMatch(lower)) return _TransferLabel.sender;
  if (RegExp(r'\bto\b').hasMatch(lower)) return _TransferLabel.receiver;
  return null;
}

({String? sender, String? receiver}) _labeledAccountTokens(
  List<OcrLine> sorted,
  int? amountY,
) {
  String? senderToken;
  String? receiverToken;
  for (final line in sorted) {
    if (amountY != null && line.yTop >= amountY) continue;
    final label = _transferLabelIn(line.text);
    if (label == null) continue;
    final token = _accountTokenIn(line.text);
    if (token == null) continue;
    switch (label) {
      case _TransferLabel.sender:
        senderToken = token;
      case _TransferLabel.receiver:
        receiverToken = token;
    }
  }
  return (sender: senderToken, receiver: receiverToken);
}

bool _labelsSwapYOrder(
  List<String> accounts,
  String labeledSender,
  String labeledReceiver,
) {
  if (accounts.length != 2) return false;
  final accountSet = {accounts[0], accounts[1]};
  if (!accountSet.contains(labeledSender) || !accountSet.contains(labeledReceiver)) {
    return false;
  }
  return labeledSender == accounts[1] && labeledReceiver == accounts[0];
}

SlipAccountParseResult parseAccountLines(
  List<OcrLine> lines, {
  bool enableLabelTieBreak = true,
}) {
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
    var sender = accounts[0];
    var receiver = accounts[1];
    if (enableLabelTieBreak) {
      final labeled = _labeledAccountTokens(sorted, amountY);
      if (labeled.sender != null &&
          labeled.receiver != null &&
          _labelsSwapYOrder(accounts, labeled.sender!, labeled.receiver!)) {
        sender = labeled.sender!;
        receiver = labeled.receiver!;
      }
    }
    return SlipAccountParseResult(
      senderAccountToken: sender,
      receiverAccountToken: receiver,
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
