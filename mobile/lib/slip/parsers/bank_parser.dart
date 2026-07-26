import '../slip_ocr.dart';
import 'slip_account_parser.dart';

abstract class BankParser {
  String get bankCode;

  bool matches(String rawText);

  ParsedSlip parse(String rawText, {List<OcrLine>? lines});
}

class ParsedSlip {
  final double? amount;
  final String? refNumber;
  final String? receiverAccountLast4;
  final String? senderAccountLast4;
  // Normalized masked account templates (masks → 'x', separators dropped),
  // e.g. "xxxxx0758x". Used to build a position-aware matcher on the PC side so
  // banks that mask the tail (KBANK) or show few digits (BBL) still match.
  final String? receiverAccountMasked;
  final String? senderAccountMasked;
  // Payee/member bank on the slip ("ไปยัง") — used to disambiguate same-amount
  // withdrawal rows. Distinct from the slip issuer bank (sender/"จาก").
  final String? receiverBank;
  final String? senderName;
  final SlipAccountConfidence accountConfidence;
  final bool valid;
  final List<String> errors;

  const ParsedSlip({
    this.amount,
    this.refNumber,
    this.receiverAccountLast4,
    this.senderAccountLast4,
    this.receiverAccountMasked,
    this.senderAccountMasked,
    this.receiverBank,
    this.senderName,
    this.accountConfidence = SlipAccountConfidence.needsReview,
    required this.valid,
    this.errors = const [],
  });
}

/// Normalize a masked account token: keep digits, turn every masking glyph into
/// lowercase 'x', and drop separators/spaces. "xxx-x-x0758-x" → "xxxxx0758x".
String normalizeMaskedAccount(String token) {
  final buffer = StringBuffer();
  for (final ch in token.split('')) {
    if (RegExp(r'[0-9]').hasMatch(ch)) {
      buffer.write(ch);
    } else if (RegExp(r'[xX\*\u2022\u00d7\u25cf]').hasMatch(ch)) {
      buffer.write('x');
    }
  }
  return buffer.toString();
}

String normalizeOcrDigits(String value) => value
    .replaceAll('O', '0')
    .replaceAll('o', '0')
    .replaceAll('l', '1')
    .replaceAll('I', '1');

bool refNumberIsValid(
  String? ref, {
  required int minRefLength,
  required int maxRefLength,
}) {
  if (ref == null) return false;
  if (ref.length < minRefLength || ref.length > maxRefLength) return false;
  if (RegExp(r'^\d+$').hasMatch(ref)) return true;
  // SCB alphanumeric refs: date-like digit prefix + mixed suffix.
  return RegExp(r'^\d[A-Za-z0-9]+$').hasMatch(ref);
}

const _amountLabels = ['จำนวนเงิน', 'จำนวน', 'Amount'];
const _refLabels = [
  'รหัสอ้างอิง',
  'เลขที่อ้างอิง',
  'เลขที่รายการ',
  'Reference',
];

/// Parsed ref span in [raw] — used to exclude ref tokens from amount harvest.
class _RefSpan {
  final String ref;
  final int start;
  final int end;

  const _RefSpan(this.ref, this.start, this.end);
}

/// True when [token] lies inside the parsed ref span or [refNumber] string.
bool tokenInsideRef({
  required String token,
  required String? refNumber,
  int tokenStart = -1,
  int refStart = -1,
  int refEnd = -1,
}) {
  if (refNumber == null || refNumber.isEmpty) return false;
  if (tokenStart >= 0 && refStart >= 0 && refEnd > refStart) {
    final tokenEnd = tokenStart + token.length;
    return tokenStart >= refStart && tokenEnd <= refEnd;
  }
  final normalized = token.replaceAll(RegExp(r'[,\.]'), '');
  return refNumber.contains(normalized);
}

bool amountLooksLikeRefFragment({
  required String amountToken,
  required String? refNumber,
  int amountStart = -1,
  int refStart = -1,
  int refEnd = -1,
}) {
  if (refNumber == null || refNumber.isEmpty) return false;
  if (tokenInsideRef(
    token: amountToken,
    refNumber: refNumber,
    tokenStart: amountStart,
    refStart: refStart,
    refEnd: refEnd,
  )) {
    return true;
  }
  // Baht integer digits only — "3,727.00" → "3727", never "372700" (cents).
  final intPart = amountToken.replaceAll(',', '').split('.').first;
  final digitsOnly = intPart.replaceAll(RegExp(r'[^0-9]'), '');
  if (digitsOnly.length >= 4 && refNumber.contains(digitsOnly)) {
    return true;
  }
  return false;
}

_RefSpan? extractRefNumber(
  String raw, {
  required int minRefLength,
  required int maxRefLength,
}) {
  _RefSpan? tryMatch(RegExp re, String window, int windowStart) {
    final match = re.firstMatch(window);
    if (match == null) return null;
    final ref = normalizeOcrDigits(match.group(0)!);
    if (!refNumberIsValid(
      ref,
      minRefLength: minRefLength,
      maxRefLength: maxRefLength,
    )) {
      return null;
    }
    return _RefSpan(ref, windowStart + match.start, windowStart + match.end);
  }

  final alphaRe = RegExp(r'[\dOolI]{9,}[A-Za-z0-9]{6,}');
  final numericRe = RegExp(r'[0-9OolI]{15,25}');

  for (final label in _refLabels) {
    var searchFrom = 0;
    while (true) {
      final idx = raw.indexOf(label, searchFrom);
      if (idx < 0) break;
      searchFrom = idx + label.length;
      final windowEnd = (idx + 120).clamp(0, raw.length);
      final window = raw.substring(idx, windowEnd);
      final hit = tryMatch(alphaRe, window, idx) ?? tryMatch(numericRe, window, idx);
      if (hit != null) return hit;
    }
  }

  final alphaHit = tryMatch(alphaRe, raw, 0);
  if (alphaHit != null) return alphaHit;
  return tryMatch(numericRe, raw, 0);
}

double? extractAmountNearLabel(
  String raw, {
  required String? refNumber,
  int refStart = -1,
  int refEnd = -1,
}) {
  final amountRe = RegExp(r'([\d,]+\.\d{2})');

  for (final label in _amountLabels) {
    var searchFrom = 0;
    while (true) {
      final idx = raw.indexOf(label, searchFrom);
      if (idx < 0) break;
      searchFrom = idx + label.length;
      final windowEnd = (idx + 120).clamp(0, raw.length);
      final window = raw.substring(idx, windowEnd);
      for (final match in amountRe.allMatches(window)) {
        final token = match.group(1)!;
        final absStart = idx + match.start;
        if (amountLooksLikeRefFragment(
          amountToken: token,
          refNumber: refNumber,
          amountStart: absStart,
          refStart: refStart,
          refEnd: refEnd,
        )) {
          continue;
        }
        final lineStart = raw.lastIndexOf('\n', absStart) + 1;
        final linePrefix = raw.substring(lineStart, absStart);
        if (linePrefix.contains('ค่าธรรมเนียม') || linePrefix.contains('Fee')) {
          continue;
        }
        final amount = double.tryParse(token.replaceAll(',', ''));
        if (amount != null && amount > 0) return amount;
      }
    }
  }
  return null;
}

/// Fallback when Thai/English amount labels are missing (common with Latin-only
/// ML Kit). Take the first money-like token that is not a ref fragment / fee.
double? extractAmountFallback(
  String raw, {
  required String? refNumber,
  int refStart = -1,
  int refEnd = -1,
}) {
  final amountRe = RegExp(r'([\d,]+\.\d{2})');
  for (final match in amountRe.allMatches(raw)) {
    final token = match.group(1)!;
    if (amountLooksLikeRefFragment(
      amountToken: token,
      refNumber: refNumber,
      amountStart: match.start,
      refStart: refStart,
      refEnd: refEnd,
    )) {
      continue;
    }
    final lineStart = raw.lastIndexOf('\n', match.start) + 1;
    final linePrefix = raw.substring(lineStart, match.start);
    if (linePrefix.contains('ค่าธรรมเนียม') || linePrefix.contains('Fee')) {
      continue;
    }
    final amount = double.tryParse(token.replaceAll(',', ''));
    if (amount != null && amount > 0) return amount;
  }
  return null;
}

/// True when [tmpl] is a masked account template (has mask glyphs), not a bare
/// digit run that could be ref leakage.
bool isRealMaskedAccountTemplate(String? tmpl) {
  if (tmpl == null || tmpl.isEmpty) return false;
  return RegExp(r'[xX\*\u2022\u00d7\u25cf]').hasMatch(tmpl);
}

/// True when [last4] only appears inside [refNumber] and is not the last4 of
/// a จาก-bound account token (masked or full).
bool senderLast4IsRefOnly({
  required String last4,
  required String refNumber,
  required String? fromBoundLast4,
}) {
  if (fromBoundLast4 == last4) return false;
  return refNumber.contains(last4);
}

/// Bank names found in OCR text, in document order. On Thai slips the first is
/// usually the payer ("จาก") and the last the payee ("ไปยัง"/member).
List<String> extractBankCodesInOrder(String raw) {
  final patterns = <(RegExp, String)>[
    (RegExp(r'กสิกร|KBANK|K\s*PLUS|K\+', caseSensitive: false), 'KBANK'),
    (RegExp(r'ไทยพาณิชย์|SCB|Siam\s*Commercial', caseSensitive: false), 'SCB'),
    (RegExp(r'กรุงเทพ|BBL|Bangkok\s*Bank', caseSensitive: false), 'BBL'),
    // กรุงไทย before กรุงเทพ already handled; avoid matching กรุงเทพ as KTB.
    (RegExp(r'กรุงไทย|KTB|Krungthai|Krung\s*Thai', caseSensitive: false), 'KTB'),
    (RegExp(r'ออมสิน|GSB|mymo|MyMo', caseSensitive: false), 'GSB'),
    (RegExp(r'ทหารไทย|ธนชาต|TTB', caseSensitive: false), 'TTB'),
    (RegExp(r'กรุงศรี|BAY', caseSensitive: false), 'BAY'),
  ];
  final hits = <({int start, String code})>[];
  for (final (re, code) in patterns) {
    for (final m in re.allMatches(raw)) {
      hits.add((start: m.start, code: code));
    }
  }
  hits.sort((a, b) => a.start.compareTo(b.start));
  // Collapse adjacent duplicates of the same code (logo + text next to it).
  final ordered = <String>[];
  for (final h in hits) {
    if (ordered.isEmpty || ordered.last != h.code) {
      ordered.add(h.code);
    }
  }
  return ordered;
}

/// First index of any [labels] in [raw], or -1.
/// English markers use word boundaries so "TO" does not hit "TOTAL"/"AUTO".
int _labelIndex(String raw, List<String> labels) {
  var best = -1;
  for (final label in labels) {
    final idx = _findLabel(raw, label);
    if (idx >= 0 && (best < 0 || idx < best)) best = idx;
  }
  return best;
}

/// Last index of any [labels] at or before [pos], or -1.
int _labelLastIndexBefore(String raw, int pos, List<String> labels) {
  var best = -1;
  final slice = raw.substring(0, pos.clamp(0, raw.length));
  for (final label in labels) {
    final idx = _findLabelLast(slice, label);
    if (idx > best) best = idx;
  }
  return best;
}

bool _isAsciiWordLabel(String label) => RegExp(r'^[A-Za-z]+$').hasMatch(label);

int _findLabel(String raw, String label) {
  if (!_isAsciiWordLabel(label)) return raw.indexOf(label);
  final re = RegExp('\\b${RegExp.escape(label)}\\b');
  return re.firstMatch(raw)?.start ?? -1;
}

int _findLabelLast(String raw, String label) {
  if (!_isAsciiWordLabel(label)) return raw.lastIndexOf(label);
  final re = RegExp('\\b${RegExp.escape(label)}\\b');
  Match? last;
  for (final m in re.allMatches(raw)) {
    last = m;
  }
  return last?.start ?? -1;
}

ParsedSlip parseSlipFields(
  String raw, {
  required int minRefLength,
  required int maxRefLength,
  List<OcrLine>? lines,
}) {
  final errors = <String>[];

  final refSpan = extractRefNumber(
    raw,
    minRefLength: minRefLength,
    maxRefLength: maxRefLength,
  );
  final ref = refSpan?.ref;
  final refStart = refSpan?.start ?? -1;
  final refEnd = refSpan?.end ?? -1;

  final amount = extractAmountNearLabel(
        raw,
        refNumber: ref,
        refStart: refStart,
        refEnd: refEnd,
      ) ??
      extractAmountFallback(
        raw,
        refNumber: ref,
        refStart: refStart,
        refEnd: refEnd,
      );
  if (amount == null || amount <= 0) {
    errors.add('amount_invalid');
  }

  if (!refNumberIsValid(
    ref,
    minRefLength: minRefLength,
    maxRefLength: maxRefLength,
  )) {
    errors.add('ref_invalid');
  }

  final ocrLines = lines ?? linesFromRawText(raw);
  final accounts = parseAccountLines(ocrLines);

  String? last4Of(String? tmpl) {
    if (tmpl == null) return null;
    final digits = tmpl.replaceAll(RegExp(r'[^0-9]'), '');
    return digits.length >= 4 ? digits.substring(digits.length - 4) : null;
  }

  String? storeAccountToken(String? token) {
    if (token == null) return null;
    final normalized = normalizeMaskedAccount(token);
    final hasMask = RegExp(r'[xX\*\u2022\u00d7\u25cf]').hasMatch(token) ||
        RegExp(r'x').hasMatch(normalized);
    if (hasMask && normalized.isNotEmpty) {
      return normalized;
    }
    final digits = token.replaceAll(RegExp(r'[^0-9]'), '');
    if (digits.length >= 8) return digits;
    return normalized.isNotEmpty ? normalized : null;
  }

  String? senderMasked;
  String? receiverMasked;
  if (accounts.confidence == SlipAccountConfidence.high) {
    senderMasked = storeAccountToken(accounts.senderAccountToken);
    receiverMasked = storeAccountToken(accounts.receiverAccountToken);
  } else {
    errors.add('accounts_needs_review');
  }

  // Payee bank = first bank after the slip-issuer bank (member side). Ignore
  // footer logos that repeat the issuer (e.g. "mymo by GSB" at the bottom).
  final banksInOrder = extractBankCodesInOrder(raw);
  String? receiverBank;
  if (banksInOrder.length >= 2) {
    final senderBank = banksInOrder.first;
    for (final code in banksInOrder.skip(1)) {
      if (code != senderBank) {
        receiverBank = code;
        break;
      }
    }
  }

  final fromBoundLast4 = last4Of(senderMasked);
  var senderLast4 = last4Of(senderMasked);
  if (ref != null &&
      senderLast4 != null &&
      senderLast4IsRefOnly(
        last4: senderLast4,
        refNumber: ref,
        fromBoundLast4: fromBoundLast4,
      )) {
    senderLast4 = fromBoundLast4;
  }

  return ParsedSlip(
    amount: amount,
    refNumber: ref,
    receiverAccountLast4: last4Of(receiverMasked),
    senderAccountLast4: senderLast4,
    receiverAccountMasked: receiverMasked,
    senderAccountMasked: senderMasked,
    receiverBank: receiverBank,
    accountConfidence: accounts.confidence,
    valid: errors.isEmpty,
    errors: errors,
  );
}
