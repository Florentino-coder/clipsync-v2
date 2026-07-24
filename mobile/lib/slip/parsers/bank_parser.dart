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
  // Payee/member bank on the slip ("ไปยัง") — used to disambiguate same-amount
  // withdrawal rows. Distinct from the slip issuer bank (sender/"จาก").
  final String? receiverBank;
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
    this.receiverBank,
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
  final maskedPositions = <int>[];
  // Fully visible account numbers (SCB shows the payee "ไปยัง" account with no
  // mask at all, e.g. "0372527587"). Kept separate from masked templates.
  final fullAccounts = <String>[];
  final fullAccountPositions = <int>[];
  final dateLike = RegExp(r'^\d{1,2}-\d{1,2}-\d{2,4}$|^\d{4}-\d{1,2}-\d{1,2}$');
  for (final match in tokenRe.allMatches(raw)) {
    final token = match.group(0)!;
    if (!hasMask.hasMatch(token)) {
      // No masking glyph — candidate for a full account number. Require 8–14
      // digits so amounts stay too short and ref numbers (15+) too long, and
      // skip date-shaped tokens like 24-07-2026.
      if (dateLike.hasMatch(token)) continue;
      final digits = token.replaceAll(RegExp(r'[^0-9]'), '');
      if (digits.length >= 8 && digits.length <= 14) {
        fullAccounts.add(digits);
        fullAccountPositions.add(match.start);
      }
      continue;
    }
    final normalized = normalizeMaskedAccount(token);
    final digitCount = normalized.replaceAll(RegExp(r'[^0-9]'), '').length;
    if (digitCount >= 2) {
      maskedTemplates.add(normalized);
      maskedPositions.add(match.start);
    }
  }

  String? last4Of(String? tmpl) {
    if (tmpl == null) return null;
    final digits = tmpl.replaceAll(RegExp(r'[^0-9]'), '');
    return digits.length >= 4 ? digits.substring(digits.length - 4) : null;
  }

  // Section labels, when OCR managed to read them. จาก = payer, ไปยัง = payee.
  // A token belongs to the section of the closest label ABOVE it.
  bool inReceiverSection(int pos) {
    final fromLast = raw.lastIndexOf('จาก', pos);
    final toLast = raw.lastIndexOf('ไปยัง', pos);
    return toLast >= 0 && toLast > fromLast;
  }

  String? senderMasked;
  String? receiverMasked;
  if (maskedTemplates.length >= 2) {
    senderMasked = maskedTemplates.first;
    receiverMasked = maskedTemplates.last;
  } else if (maskedTemplates.length == 1) {
    if (fullAccounts.isNotEmpty) {
      // SCB-style slip: the payer ("จาก") account is the only masked token and
      // the payee ("ไปยัง") account is printed in full. Prefer the จาก/ไปยัง
      // labels when readable; otherwise assume masked=payer, full=payee.
      final maskedIsReceiver = inReceiverSection(maskedPositions.first);
      if (maskedIsReceiver) {
        receiverMasked = maskedTemplates.first;
        senderMasked = fullAccounts.first;
      } else {
        senderMasked = maskedTemplates.first;
        // Pick the full account in the ไปยัง section when labels are readable,
        // else the last one (payee is listed last on Thai slips).
        var receiverFull = fullAccounts.last;
        for (var i = 0; i < fullAccounts.length; i++) {
          if (inReceiverSection(fullAccountPositions[i])) {
            receiverFull = fullAccounts[i];
            break;
          }
        }
        receiverMasked = receiverFull;
      }
    } else {
      receiverMasked = maskedTemplates.first;
    }
  }
  if (receiverMasked == null) {
    errors.add('last4_missing');
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

  return ParsedSlip(
    amount: amount,
    refNumber: ref,
    receiverAccountLast4: last4Of(receiverMasked),
    senderAccountLast4: last4Of(senderMasked),
    receiverAccountMasked: receiverMasked,
    senderAccountMasked: senderMasked,
    receiverBank: receiverBank,
    valid: errors.isEmpty,
    errors: errors,
  );
}
