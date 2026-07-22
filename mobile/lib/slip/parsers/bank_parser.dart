abstract class BankParser {
  String get bankCode;

  bool matches(String rawText);

  ParsedSlip parse(String rawText);
}

class ParsedSlip {
  final double? amount;
  final String? refNumber;
  final String? receiverAccountLast4;
  final String? senderName;
  final bool valid;
  final List<String> errors;

  const ParsedSlip({
    this.amount,
    this.refNumber,
    this.receiverAccountLast4,
    this.senderName,
    required this.valid,
    this.errors = const [],
  });
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

  final last4Match = RegExp(r'[xX\*]{1,6}[- ]?(\d{4})').firstMatch(raw);
  final last4 = last4Match?.group(1);
  if (last4 == null) {
    errors.add('last4_missing');
  }

  return ParsedSlip(
    amount: amount,
    refNumber: ref,
    receiverAccountLast4: last4,
    valid: errors.isEmpty,
    errors: errors,
  );
}
