#!/usr/bin/env python
#
# Copyright 2008 Google Inc.  All Rights Reserved.
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

"""Generate Google Mock classes from base classes.

This program will read in a C++ source file and output the Google Mock
classes for the specified classes.  If no class is specified, all
classes in the source file are emitted.

Output is sent to stdout.
"""

__author__ = 'nnorwitz@google.com (Neal Norwitz)'


import os
import re
import sys
import argparse

from cpp import ast
from cpp import utils

# Preserve compatibility with Python 2.3.
try:
  _dummy = set
except NameError:
  import sets
  set = sets.Set

_VERSION = (1, 1, 0)  # The version of this script.
# How many spaces to indent.  Can set me with the INDENT environment variable.
_INDENT = 2


def _CompatibleNamespace(derived, parent):
  """

  Args:
    derived: The Class node for the child class.
    parent: The Type node for a base class, from the child class's node.bases.

  Returns:
    True if  there is no conflict in the namespaces to the extent that the parent specifies one,
    they are compatible. This is not a very strict check, but for many programs it will work.

  """
  return cmp(derived.namespace[0:len(parent.namespace)], parent.namespace) == 0


def _BaseClass(class_node, base_type, ast_list):
  """

  Args:
    class_node: The Class node to examine.
    parent: The Type node for a base class, from the child class's node.bases.
    ast_list: The AST for the entire file.

  Returns:
    The Class node for the base class, if any.

  """
  for node in ast_list:
    if isinstance(node, ast.Class) and node.body and node.name == base_type.name and _CompatibleNamespace(class_node, node):
      return node
  return None


def _GenerateMethods(output_lines, source, class_node, ast_list, seen, do_bases):
  function_type = (ast.FUNCTION_VIRTUAL | ast.FUNCTION_PURE_VIRTUAL |
                   ast.FUNCTION_OVERRIDE)
  ctor_or_dtor = ast.FUNCTION_CTOR | ast.FUNCTION_DTOR
  indent = ' ' * _INDENT

  for node in class_node.body:
    # We only care about virtual functions.
    if (isinstance(node, ast.Function) and
        node.modifiers & function_type and
        not node.modifiers & ctor_or_dtor):
      # Pick out all the elements we need from the original function.
      const = ''
      if node.modifiers & ast.FUNCTION_CONST:
        const = 'CONST_'
      return_type = 'void'
      if node.return_type:
        # Add modifiers like 'const'.
        modifiers = ''
        if node.return_type.modifiers:
          modifiers = ' '.join(node.return_type.modifiers) + ' '
        return_type = modifiers + node.return_type.name
        template_args = [arg.name for arg in node.return_type.templated_types]
        if template_args:
          return_type += '<' + ', '.join(template_args) + '>'
          if len(template_args) > 1:
            for line in [
                '// The following line won\'t really compile, as the return',
                '// type has multiple template arguments.  To fix it, use a',
                '// typedef for the return type.']:
              output_lines.append(indent + line)
        if node.return_type.pointer:
          return_type += '*'
        if node.return_type.reference:
          return_type += '&'
        num_parameters = len(node.parameters)
        if len(node.parameters) == 1:
          first_param = node.parameters[0]
          if source[first_param.start:first_param.end].strip() == 'void':
            # We must treat T(void) as a function with no parameters.
            num_parameters = 0
      tmpl = ''
      if class_node.templated_types:
        tmpl = '_T'
      mock_method_macro = 'MOCK_%sMETHOD%d%s' % (const, num_parameters, tmpl)

      args = ''
      if node.parameters:
        # Due to the parser limitations, it is impossible to keep comments
        # while stripping the default parameters.  When defaults are
        # present, we choose to strip them and comments (and produce
        # compilable code).
        # TODO(nnorwitz@google.com): Investigate whether it is possible to
        # preserve parameter name when reconstructing parameter text from
        # the AST.
        if len([param for param in node.parameters if param.default]) > 0:
          args = ', '.join(param.type.name for param in node.parameters)
        else:
          # Get the full text of the parameters from the start
          # of the first parameter to the end of the last parameter.
          start = node.parameters[0].start
          end = node.parameters[-1].end
          # Remove // comments.
          args_strings = re.sub(r'//.*', '', source[start:end])
          # Condense multiple spaces and eliminate newlines putting the
          # parameters together on a single line.  Ensure there is a
          # space in an argument which is split by a newline without
          # intervening whitespace, e.g.: int\nBar
          args = re.sub('  +', ' ', args_strings.replace('\n', ' '))

      # Create the mock method definition.
      decl = '%s%s(%s,' % (indent, mock_method_macro, node.name)
      args = '%s%s(%s));' % (indent * 3, return_type, args)
      # Do not re-generate a mock for something we've printed before.
      if not seen.has_key(decl+args):
        output_lines.extend([decl, args])
        seen[decl+args] = True

  try:
    if do_bases:
      # Generate mocks for inherited functions.
      for base_type in class_node.bases:
        base_class = _BaseClass(class_node, base_type, ast_list)
        if base_class:
          output_lines.extend(["%s// Inherited from %s" % (indent, base_class.FullName())])
          _GenerateMethods(output_lines, source, base_class, ast_list, seen, do_bases)
  except:
    pass


def _GenerateMocks(filename, source, ast_list, desired_class_names, do_bases=True):
  processed_class_names = set()
  lines = []
  for node in ast_list:
    if (isinstance(node, ast.Class) and node.body and
        # desired_class_names being None means that all classes are selected.
        (not desired_class_names or node.name in desired_class_names)):
      class_name = node.name
      parent_name = class_name
      processed_class_names.add(class_name)
      class_node = node
      # Add namespace before the class.
      if class_node.namespace:
        lines.extend(['namespace %s {' % n for n in class_node.namespace])  # }
        lines.append('')

      # Add template args for templated classes.
      if class_node.templated_types:
        # TODO(paulchang): The AST doesn't preserve template argument order,
        # so we have to make up names here.
        # TODO(paulchang): Handle non-type template arguments (e.g.
        # template<typename T, int N>).
        template_arg_count = len(class_node.templated_types.keys())
        template_args = ['T%d' % n for n in range(template_arg_count)]
        template_decls = ['typename ' + arg for arg in template_args]
        lines.append('template <' + ', '.join(template_decls) + '>')
        parent_name += '<' + ', '.join(template_args) + '>'

      # Add the class prolog.
      lines.append('class Mock%s : public %s {'  # }
                   % (class_name, parent_name))
      lines.append('%spublic:' % (' ' * (_INDENT // 2)))

      # Add all the methods.
      _GenerateMethods(lines, source, class_node, ast_list, {}, do_bases)

      # Close the class.
      if lines:
        # If there are no virtual methods, no need for a public label.
        if len(lines) == 2:
          del lines[-1]

        # Only close the class if there really is a class.
        lines.append('};')
        lines.append('')  # Add an extra newline.

      # Close the namespace.
      if class_node.namespace:
        for i in range(len(class_node.namespace)-1, -1, -1):
          lines.append('}  // namespace %s' % class_node.namespace[i])
        lines.append('')  # Add an extra newline.

  if desired_class_names:
    missing_class_name_list = list(desired_class_names - processed_class_names)
    if missing_class_name_list:
      missing_class_name_list.sort()
      sys.stderr.write('Class(es) not found in %s: %s\n' %
                       (filename, ', '.join(missing_class_name_list)))
  elif not processed_class_names:
    sys.stderr.write('No class found in %s\n' % filename)

  return lines


def main(argv=sys.argv):
  parser = argparse.ArgumentParser(description="Simple generator for gmock functions", epilog=__doc__)
  parser.add_argument('--bases', dest='bases', action='store_true', help='include functions from base classes')
  parser.add_argument('header', nargs='?', help='header file', default='')
  parser.add_argument('classes', metavar='class_name', nargs='*', help='generate mocks for only these classes')
  args = parser.parse_args()
  if not args.header:
    sys.stderr.write('Google Mock Class Generator v%s\n\n' %
                     '.'.join(map(str, _VERSION)))
    parser.print_help()
    return 1

  global _INDENT
  try:
    _INDENT = int(os.environ['INDENT'])
  except KeyError:
    pass
  except:
    sys.stderr.write('Unable to use indent of %s\n' % os.environ.get('INDENT'))

  filename = args.header
  desired_class_names = set(args.classes) # None means all classes in the source file.
  do_bases = args.bases
  source = utils.ReadFile(filename)
  if source is None:
    return 1

  builder = ast.BuilderFromSource(source, filename)
  try:
    entire_ast = filter(None, builder.Generate())
  except KeyboardInterrupt:
    return
  except:
    # An error message was already printed since we couldn't parse.
    sys.exit(1)
  else:
    lines = _GenerateMocks(filename, source, entire_ast, desired_class_names, do_bases)
    sys.stdout.write('\n'.join(lines))


if __name__ == '__main__':
  main(sys.argv)
