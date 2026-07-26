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

// Claude-style dash groups; {1,7} so middle segments like xxx690 match.
final _accountDashRe = RegExp(
  r'[xX0-9*\u2022\u00d7\u25cf]{1,4}(-[xX0-9*\u2022\u00d7\u25cf]{1,7}){1,4}',
);
final _shortMaskRe = RegExp(r'[xX*]{1,6}-?\d{3,4}|\*{1,6}\d{3,4}');
final _fullAccountRe = RegExp(r'(?<![0-9])\d{8,14}(?![0-9])');
final _dateLike = RegExp(r'^\d{1,2}-\d{1,2}-\d{2,4}$|^\d{4}-\d{1,2}-\d{1,2}$');

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

bool _isFeeLine(String text) =>
    text.contains('ค่าธรรมเนียม') ||
    text.toLowerCase().contains('fee');

bool _looksLikeRefLine(String text) {
  final lower = text.toLowerCase();
  return text.contains('รหัสอ้างอิง') ||
      text.contains('เลขที่อ้างอิง') ||
      text.contains('เลขที่รายการ') ||
      text.contains('หมายเลขอ้างอิง') ||
      lower.contains('reference') ||
      RegExp(r'\bref\b').hasMatch(lower);
}

bool _tokenOk(String token) {
  if (_dateLike.hasMatch(token)) return false;
  final digits = token.replaceAll(RegExp(r'[^0-9]'), '');
  if (digits.length < 2 || digits.length >= 15) return false;
  return true;
}

String? _accountTokenIn(String text) {
  if (_isFeeLine(text)) return null;
  // Alphanumeric bank refs often contain Latin X — never treat as accounts.
  if (_looksLikeRefLine(text)) return null;

  final dash = _accountDashRe.firstMatch(text);
  if (dash != null) {
    final token = dash.group(0)!;
    if (_tokenOk(token)) return token;
  }

  // Require 2+ consecutive mask glyphs so refs like 202607268XRZL… do not match.
  final undashed = RegExp(
    r'[0-9]*[xX*\u2022\u00d7\u25cf]{2,}[0-9xX*\u2022\u00d7\u25cf]*',
  ).firstMatch(text);
  if (undashed != null) {
    final token = undashed.group(0)!;
    if (token.length >= 6 && _tokenOk(token)) {
      return token;
    }
  }

  final short = _shortMaskRe.firstMatch(text);
  if (short != null) {
    final token = short.group(0)!;
    if (_tokenOk(token)) return token;
  }

  // Full undashed account (SCB payee often printed in full).
  if (!_lineIsAmount(text)) {
    final full = _fullAccountRe.firstMatch(text);
    if (full != null) {
      final token = full.group(0)!;
      // Reject digit runs that continue into letters (ref blobs).
      final after = text.substring(full.end);
      if (after.isNotEmpty && RegExp(r'^[A-Za-z]').hasMatch(after)) {
        return null;
      }
      if (_tokenOk(token) && token.length >= 8) return token;
    }
  }

  return null;
}

enum _TransferLabel { sender, receiver }

_TransferLabel? _transferLabelIn(String text) {
  if (text.contains('จาก')) return _TransferLabel.sender;
  if (text.contains('ไปยัง') || text.contains('ไปที่') || text.contains('ถึง')) {
    return _TransferLabel.receiver;
  }
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
    final label = _transferLabelIn(line.text);
    if (label == null) continue;
    final token = _accountTokenIn(line.text);
    if (token == null) continue;
    // Labels may sit on amount-at-top layouts; do not hard-filter by amountY.
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
  if (!accountSet.contains(labeledSender) ||
      !accountSet.contains(labeledReceiver)) {
    return false;
  }
  return labeledSender == accounts[1] && labeledReceiver == accounts[0];
}

List<String> _chooseAccountCandidates({
  required List<String> aboveAmount,
  required List<String> allAccounts,
}) {
  // Prefer classic layout: accounts above the amount line.
  if (aboveAmount.length == 2) return aboveAmount;
  // Amount-at-top banks (BBL/GSB): both accounts sit below จำนวนเงิน.
  if (allAccounts.length == 2) return allAccounts;
  // Incomplete / noisy — report what we saw for NEEDS_REVIEW debugging.
  if (aboveAmount.isNotEmpty) return aboveAmount;
  return allAccounts;
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

  final above = <String>[];
  final all = <String>[];
  for (final line in sorted) {
    final token = _accountTokenIn(line.text);
    if (token == null) continue;
    all.add(token);
    if (amountY == null || line.yTop < amountY) {
      above.add(token);
    }
  }

  final accounts = _chooseAccountCandidates(
    aboveAmount: above,
    allAccounts: all,
  );

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
