# Lint as: python3
#
# Copyright 2020 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Helper utilities for asserting DSLX interpreter/LLVM IR JIT equivalence."""

from typing import Tuple, Text, Optional, Dict, Iterable

from absl import logging

from xls.dslx import ast
from xls.ir.python import llvm_ir_jit
from xls.ir.python import value as ir_value
from xls.ir.python import bits as ir_bits
from xls.ir.python import number_parser
from xls.dslx import bit_helpers
from xls.dslx.concrete_type import ArrayType
from xls.dslx.concrete_type import BitsType
from xls.dslx.concrete_type import ConcreteType
from xls.dslx.concrete_type import EnumType
from xls.dslx.concrete_type import TupleType
from xls.dslx.interpreter import value as dslx_value

WORD_SIZE = 64  # type: int

class UnsupportedConversionError(Exception):
  """Raised when the JIT bindings throw an exception.

  This exception is caught in interpreter.evaluate_fn() and means
  that the function in question isn't JIT convertable (yet).
  """

def convert_interpreter_value_to_ir(
    interpreter_value: dslx_value.Value) -> ir_value.Value:
  """Recursively translates a DSLX Value into an IR Value."""
  if interpreter_value.is_bits() or interpreter_value.is_enum():
    return ir_value.Value(
        _int_to_bits(interpreter_value.get_bits_value_check_sign(),
                     interpreter_value.get_bit_count()))
  elif interpreter_value.is_array():
    ir_arr = []
    for e in interpreter_value.array_payload.elements:
        ir_arr.append(convert_interpreter_value_to_ir(e))
    return ir_value.Value.make_array(ir_arr)
  elif interpreter_value.is_tuple():
    ir_tuple = []
    for e in interpreter_value.tuple_members:
      ir_tuple.append(convert_interpreter_value_to_ir(e))
    return ir_value.Value.make_tuple(ir_tuple)
  else:
    logging.vlog(3, "Can't convert to JIT value: %s", interpreter_value)
    raise UnsupportedConversionError

def convert_args_to_ir(
    args: Iterable[dslx_value.Value]) -> Iterable[ir_value.Value]:
  ir_args = []
  for arg in args:
    ir_args.append(convert_interpreter_value_to_ir(arg))

  return ir_args

def get_bits(jit_bits: ir_bits.Bits, signed: bool) -> int:
  """Constructs the ir bits value by reading in a 64-bit value at a time."""
  bit_count = jit_bits.bit_count()
  bits_value = 0
  word_number = 0
  while (word_number * 64) < bit_count:
    word_value = jit_bits.word_to_uint(word_number)
    bits_value = (word_value << (word_number * WORD_SIZE)) | bits_value
    word_number += 1

  return (bits_value if not signed
          else bit_helpers.from_twos_complement(bits_value, bit_count))

def compare_values(interpreter_value: dslx_value.Value,
                   jit_value: ir_value.Value):
  """Asserts equality between a DSLX Value and an IR Value.

  Recursively traverses the values (for arrays/tuples) and makes assertions
  about value and length properties.
  """
  if interpreter_value.is_bits() or interpreter_value.is_enum():
    assert jit_value.is_bits()

    jit_value = jit_value.get_bits()
    bit_count = interpreter_value.get_bit_count()
    assert bit_count == jit_value.bit_count()

    if interpreter_value.is_ubits():
      interpreter_bits_value = interpreter_value.get_bits_value()
      jit_bits_value = get_bits(jit_value, signed=False)
    else:
      interpreter_bits_value = interpreter_value.get_bits_value_signed()
      jit_bits_value = get_bits(jit_value, signed=True)
    assert interpreter_bits_value == jit_bits_value

  elif interpreter_value.is_array():
    assert jit_value.is_array()

    interpreter_values = interpreter_value.array_payload.elements
    jit_values = jit_value.get_elements()
    assert len(interpreter_values) == len(jit_values)

    for interpreter_element, jit_element in zip(interpreter_values, jit_values):
      compare_values(interpreter_element, jit_element)

  elif interpreter_value.is_tuple():
    assert jit_value.is_tuple()

    interpreter_values = interpreter_value.tuple_members
    jit_values = jit_value.get_elements()
    assert len(interpreter_values) == len(jit_values)

    for interpreter_element, jit_element in zip(interpreter_values, jit_values):
      compare_values(interpreter_element, jit_element)

  else:
    logging.vlog(3, "No JIT-supported type equivalent: %s", interpreter_value)
    raise UnsupportedConversionError

def _int_to_bits(value: int, bit_count: int) -> ir_bits.Bits:
  """Converts a Python arbitrary precision int to a Bits type."""
  if bit_count <= WORD_SIZE:
    return ir_bits.UBits(value, bit_count) if value >= 0 else ir_bits.SBits(
        value, bit_count)
  return number_parser.bits_from_string(
      bit_helpers.to_hex_string(value, bit_count), bit_count=bit_count)
