abstract class BankParser {
  String get bankCode;

  bool matches(String rawText);

  ParsedSlip parse(String rawText);
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
  final String? senderName;
  final bool valid;
  final List<String> errors;

  const ParsedSlip({
    this.amount,
    this.refNumber,
    this.receiverAccountLast4,
    this.senderAccountLast4,
    this.receiverAccountMasked,
    this.senderAccountMasked,
    this.senderName,
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

ParsedSlip parseSlipFields(
  String raw, {
  required int minRefLength,
  required int maxRefLength,
}) {
  final errors = <String>[];

  double? amount;
  final amountMatch = RegExp(r'([\d,]+\.\d{2})').firstMatch(raw);
  if (amountMatch != null) {
    amount = double.tryParse(amountMatch.group(1)!.replaceAll(',', ''));
  }
  if (amount == null || amount <= 0) {
    errors.add('amount_invalid');
  }

  String? ref;
  final refMatch = RegExp(r'[0-9OolI]{15,25}').firstMatch(raw);
  if (refMatch != null) {
    ref = normalizeOcrDigits(refMatch.group(0)!);
  }
  final refPattern = RegExp('^\\d{$minRefLength,$maxRefLength}\$');
  if (ref == null || !refPattern.hasMatch(ref)) {
    errors.add('ref_invalid');
  }

  // Masked account tokens in document order. On Thai slips the payer ("จาก")
  // is listed first and the payee ("ไปยัง") last.
  //
  // ML Kit runs Latin-only OCR so the Thai words จาก/ไปยัง are unreliable — we
  // anchor on position. A token may START with a digit (BBL "584-0-xxx518",
  // GSB "0203xxxx7778") or a mask; it must contain at least one masking glyph
  // (so long numeric ref numbers are never mistaken for accounts) and >=2
  // visible digits. Separators are allowed inside the token.
  final tokenRe =
      RegExp(r'[0-9xX\*\u2022\u00d7\u25cf][0-9xX\*\u2022\u00d7\u25cf\-]{4,}');
  final hasMask = RegExp(r'[xX\*\u2022\u00d7\u25cf]');
  final maskedTemplates = <String>[];
  for (final match in tokenRe.allMatches(raw)) {
    final token = match.group(0)!;
    if (!hasMask.hasMatch(token)) continue;
    final normalized = normalizeMaskedAccount(token);
    final digitCount = normalized.replaceAll(RegExp(r'[^0-9]'), '').length;
    if (digitCount >= 2) {
      maskedTemplates.add(normalized);
    }
  }

  String? last4Of(String? tmpl) {
    if (tmpl == null) return null;
    final digits = tmpl.replaceAll(RegExp(r'[^0-9]'), '');
    return digits.length >= 4 ? digits.substring(digits.length - 4) : null;
  }

  String? senderMasked;
  String? receiverMasked;
  if (maskedTemplates.length >= 2) {
    senderMasked = maskedTemplates.first;
    receiverMasked = maskedTemplates.last;
  } else if (maskedTemplates.length == 1) {
    receiverMasked = maskedTemplates.first;
  }
  if (receiverMasked == null) {
    errors.add('last4_missing');
  }

  return ParsedSlip(
    amount: amount,
    refNumber: ref,
    receiverAccountLast4: last4Of(receiverMasked),
    senderAccountLast4: last4Of(senderMasked),
    receiverAccountMasked: receiverMasked,
    senderAccountMasked: senderMasked,
    valid: errors.isEmpty,
    errors: errors,
  );
}
