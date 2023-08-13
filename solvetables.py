import argparse
import ipaddress
import re
from z3 import *


def create_iptables_argparse() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="iptables")
    parser.add_argument("-A", "--append")
    parser.add_argument("-p", "--protocol", default="all")

    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("-s", "--source", default="0.0.0.0/0")
    source_group.add_argument("-ns", "--not-source")

    destination_group = parser.add_mutually_exclusive_group()
    destination_group.add_argument("-d", "--destination", default="0.0.0.0/0")
    destination_group.add_argument("-nd", "--not-destination")

    parser.add_argument("-j", "--jump")

    if_group = parser.add_mutually_exclusive_group()
    if_group.add_argument("-i", "--in-interface")
    if_group.add_argument("-ni", "--not-in-interface")

    of_group = parser.add_mutually_exclusive_group()
    of_group.add_argument("-o", "--out-interface")
    of_group.add_argument("-no", "--not-out-interface")

    sport_group = parser.add_mutually_exclusive_group()
    sport_group.add_argument("--sport", default="0:655335")
    sport_group.add_argument("--sports", dest="sport")

    dport_group = parser.add_mutually_exclusive_group()
    dport_group.add_argument("--dport", default="0:655335")
    dport_group.add_argument("--dports", dest="dport")

    state_group = parser.add_mutually_exclusive_group()
    parser.add_argument("--state")
    parser.add_argument("--ctstate", dest="state")

    parser.add_argument("-m", "--match")
    parser.add_argument("--tcp-flags", nargs=2)
    parser.add_argument("--icmp-type")
    parser.add_argument("--set", action="store_true")
    parser.add_argument("--name")
    parser.add_argument("--mask")
    parser.add_argument("--rsource", action="store_true")
    parser.add_argument("--rcheck", action="store_true")
    parser.add_argument("--seconds")
    parser.add_argument("-f", "--fragment")
    parser.add_argument("-c", "--set-counters")
    return parser


class Rule:
    PROTOCOL_ENUM = [
        "all",
        "tcp",
        "udp",
        "udplite",
        "icmp",
        "icmpv6",
        "esp",
        "ah",
        "sctp",
        "mh",
    ]
    CHAIN_ENUM = ["INPUT", "FORWARD", "OUTPUT"]
    STATE_ENUM = ["NEW", "RELATED", "ESTABLISHED"]
    INTERFACE_ENUM = []
    IPTABLES_PARSER = create_iptables_argparse()

    def __init__(self, rule: str):
        self.iptables_rule = rule
        rule = self._fix_not_rule(rule)
        self.args = self.IPTABLES_PARSER.parse_args(rule.split())
        self.constraints = None

    def get_target(self):
        return self.args.jump

    def _fix_not_rule(self, rule: str) -> str:
        return rule.replace("! --", "--not-").replace("! -", "-n")

    @classmethod
    def _get_or_add_interface_index(cls, interface: str) -> int:
        if interface not in cls.INTERFACE_ENUM:
            cls.INTERFACE_ENUM.append(interface)
        return cls.INTERFACE_ENUM.index(interface)

    def _create_ip_constraints(
        self, var: BitVecRef, ip: str, invert: bool = False
    ) -> list[BoolRef]:
        cidr = ipaddress.ip_network(ip)
        constraints = [
            ULE(int(cidr[0]), var),
            ULE(var, int(cidr[-1])),
        ]
        if invert:
            constraints = [Not(c) for c in constraints]
        return constraints

    def _create_interface_constraints(
        self, var: BitVecRef, interface: str, invert: bool = False
    ) -> list[BoolRef]:
        if interface is None:
            return []
        else:
            constraint = var == self._get_or_add_interface_index(interface)
            if invert:
                constraint = Not(constraint)
            return [constraint]

    def _create_protocol_constraints(
        self, var: BitVecRef, protocol: str
    ) -> list[BoolRef]:
        protocol_index = self.PROTOCOL_ENUM.index(protocol)
        if protocol_index == 0:
            return []
        else:
            return [var == protocol_index]

    def _create_port_constraints(self, var: BitVecRef, port: str) -> list[BoolRef]:
        if ":" in port:
            port_range = port.split(":")
            port_min = int(port_range[0])
            port_max = int(port_range[-1])
            return [
                ULE(port_min, var),
                ULE(var, port_max),
            ]
        elif "," in port:
            ports = port.split(",")
            return [Or([var == p for p in ports])]
        else:
            return [var == int(port)]

    def _create_state_constraints(self, var: BitVecRef, state: str) -> list[BoolRef]:
        states = []
        for s in state.split(","):
            states.append(self.STATE_ENUM.index(s))
        return [Or([var == s for s in states])]

    def _build_constraints(self, st: "SolveTables"):
        sub_constraints = []
        if self.args.jump in ["ACCEPT", "REJECT", "DROP"]:
            if self.args.not_source:
                sub_constraints += self._create_ip_constraints(
                    st.src_ip_model, self.args.not_source, invert=True
                )
            else:
                sub_constraints += self._create_ip_constraints(
                    st.src_ip_model, self.args.source
                )
            if self.args.not_source:
                sub_constraints += self._create_ip_constraints(
                    st.dst_ip_model, self.args.not_destination, invert=True
                )
            else:
                sub_constraints += self._create_ip_constraints(
                    st.dst_ip_model, self.args.destination
                )
            if self.args.not_in_interface:
                sub_constraints += self._create_interface_constraints(
                    st.input_interface_model, self.args.not_in_interface, invert=True
                )
            else:
                sub_constraints += self._create_interface_constraints(
                    st.input_interface_model, self.args.in_interface
                )
            if self.args.not_out_interface:
                sub_constraints += self._create_interface_constraints(
                    st.output_interface_model,
                    self.args.not_out_interface,
                    invert=True,
                )
            else:
                sub_constraints += self._create_interface_constraints(
                    st.output_interface_model, self.args.out_interface
                )
            sub_constraints += self._create_protocol_constraints(
                st.protocol_model, self.args.protocol
            )
            sub_constraints += self._create_port_constraints(
                st.src_port_model, self.args.sport
            )
            sub_constraints += self._create_port_constraints(
                st.dst_port_model, self.args.dport
            )
            if self.args.state is not None:
                sub_constraints += self._create_state_constraints(
                    st.state_model, self.args.state
                )

            constraints = And(sub_constraints)
            constraints = simplify(constraints)
            # print("adding constraints:", constraints)
            self.constraints = constraints

    def get_constraints(self, st: "SolveTables") -> BoolRef:
        if self.constraints is None:
            self._build_constraints(st)
        return self.constraints


class SolveTables:
    def __init__(self, default_policy: str) -> None:
        self.accept_default = default_policy == "ACCEPT"
        self.rules: list[Rule] = []
        self.src_ip_model: BitVecRef = BitVec("src_ip_model", 32)
        self.dst_ip_model: BitVecRef = BitVec("dst_ip_model", 32)
        self.input_interface_model: BitVecRef = BitVec("input_interface_model", 8)
        self.output_interface_model: BitVecRef = BitVec("output_interface_model", 8)
        self.protocol_model: BitVecRef = BitVec("protocol_model", 4)
        self.src_port_model: BitVecRef = BitVec("src_port_model", 16)
        self.dst_port_model: BitVecRef = BitVec("dst_port_model", 16)
        self.state_model: BitVecRef = BitVec("state_model", 4)
        self.iptables_parser: argparse.ArgumentParser = create_iptables_argparse()

    def add_rule(self, rule: str):
        self.rules.append(Rule(rule))

    def _get_base_constraints(self) -> Probe | BoolRef:
        base_rules = And(
            ULT(self.protocol_model, len(Rule.PROTOCOL_ENUM)),
            ULT(self.input_interface_model, len(Rule.INTERFACE_ENUM)),
            ULT(self.output_interface_model, len(Rule.INTERFACE_ENUM)),
            ULT(self.state_model, len(Rule.STATE_ENUM)),
        )
        return base_rules

    def build_constraints(self) -> Probe | BoolRef:
        # print("self.constraints:", self.constraints)
        previous_rules = []
        rules = []
        for rule in self.rules:
            target = rule.get_target()
            constraints = rule.get_constraints(self)
            if target == "ACCEPT":
                if previous_rules:
                    rules.append(And(Not(Or(previous_rules)), constraints))
                else:
                    rules.append(constraints)
            if constraints is not None:
                previous_rules.append(constraints)
        if self.accept_default:
            rules.append(True)
        base_rules = self._get_base_constraints()

        # return And(Or(rules), base_rules)
        return simplify(And(Or(rules), base_rules))

    def check_and_get_model(self, constraints: (Probe | BoolRef)) -> None | ModelRef:
        m = None
        s = Solver()
        rules = self.build_constraints()
        # print("rules:", rules)
        s.add(constraints, rules)
        result = s.check()
        if result == sat:
            m = s.model()
        return m

    def translate_model(self, model: ModelRef):
        protocol_index = (
            model.eval(self.protocol_model, model_completion=True).as_long()
            if model[self.protocol_model] is not None
            else 0
        )
        translated_model = {
            "src_ip": ipaddress.ip_address(
                model.eval(self.src_ip_model, model_completion=True).as_long()
            ),
            "dst_ip": ipaddress.ip_address(
                model.eval(self.dst_ip_model, model_completion=True).as_long()
            ),
            "input_interface": Rule.INTERFACE_ENUM[
                model.eval(self.input_interface_model, model_completion=True).as_long()
            ],
            "output_interface": Rule.INTERFACE_ENUM[
                model.eval(self.output_interface_model, model_completion=True).as_long()
            ],
            "protocol": Rule.PROTOCOL_ENUM[protocol_index],
            "src_port": model.eval(
                self.src_port_model, model_completion=True
            ).as_long(),
            "dst_port": model.eval(
                self.dst_port_model, model_completion=True
            ).as_long(),
            "state": Rule.STATE_ENUM[
                model.eval(self.state_model, model_completion=True).as_long()
            ],
        }
        return translated_model

    def identify_rule(self, model: ModelRef) -> None | str:
        s = Solver()
        for rule in self.rules:
            rule_constraints = rule.get_constraints(self)
            if rule_constraints is not None:
                s.add(rule_constraints)
                s.add(self._get_base_constraints())
                for var in [
                    self.src_ip_model,
                    self.dst_ip_model,
                    self.input_interface_model,
                    self.output_interface_model,
                    self.protocol_model,
                    self.src_port_model,
                    self.dst_port_model,
                    self.state_model,
                ]:
                    if model[var] is not None:
                        s.add(var == model[var])
                if s.check() == sat:
                    return rule.iptables_rule
            s.reset()

    def translate_expression(self, expression: list[str]) -> Probe | BoolRef:
        var_table = {
            "src_ip": self.src_ip_model,
            "dst_ip": self.dst_ip_model,
            "in_iface": self.input_interface_model,
            "out_iface": self.output_interface_model,
            "protocol": self.protocol_model,
            "src_port": self.src_port_model,
            "dst_port": self.dst_port_model,
            "state": self.state_model,
        }
        op_table = {
            "==": BitVecRef.__eq__,
            "!=": BitVecRef.__ne__,
            "<=": ULE,
            ">=": UGE,
            "<": ULT,
            ">": UGT,
        }
        concat_op_table = {
            "and": And,
            "or": Or,
        }

        constraints = None
        concat_op = None

        while len(expression) > 0:
            assert len(expression) >= 3

            operand1 = expression.pop(0)
            operator = expression.pop(0)
            operand2 = expression.pop(0)

            # assert operand1 in var_table.keys()
            top1 = var_table[operand1]

            # assert operator in op_table.keys()
            op = op_table[operator]

            match operand1.split("_"):
                case ["state"]:
                    top2 = Rule.STATE_ENUM.index(operand2)
                case ["protocol"]:
                    top2 = Rule.PROTOCOL_ENUM.index(operand2)
                case [_, "iface"]:
                    top2 = Rule._get_or_add_interface_index(operand2)
                case [_, "port"]:
                    top2 = int(operand2)
                case [_, "ip"]:
                    top2 = int(ipaddress.IPv4Address(operand2))

            sub_constraint = op(top1, top2)
            if constraints is None:
                constraints = sub_constraint
            else:
                constraints = concat_op(constraints, sub_constraint)

            if len(expression) > 0:
                concat_operator = expression.pop(0)
                # assert concat_operator in concat_op_table.keys()
                concat_op = concat_op_table[concat_operator]
        return constraints


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-p", "--default-policy", default=None, choices=["ACCEPT", "DROP", "REJECT"]
    )
    parser.add_argument("chain", choices=["INPUT", "FORWARD", "OUTPUT"])
    parser.add_argument("iptables_save_log", type=argparse.FileType("r"))
    parser.add_argument("expression", nargs="+")
    args = parser.parse_args()

    iptables_rules_file = args.iptables_save_log.read()

    default_policy = args.default_policy
    if default_policy is None:
        match = re.search(
            f"^:{args.chain}\s+(?P<default_policy>(ACCEPT|DROP|REJECT))",
            iptables_rules_file,
            re.M,
        )
        if match is None:
            parser.error(
                f"Unable to detect default policy for {args.chain}, please specify with --default-policy"
            )
        else:
            default_policy = match.group("default_policy")
            print(f"identified default policy for {args.chain} is {default_policy}")
    st = SolveTables(default_policy=default_policy)

    for rule_line in iptables_rules_file.splitlines():
        if rule_line.startswith(f"-A {args.chain}"):
            # print(rule_line)
            st.add_rule(rule_line)

    expression = (
        args.expression[0].split() if len(args.expression) == 1 else args.expression
    )
    additional_constraints = st.translate_expression(expression)
    model = st.check_and_get_model(constraints=additional_constraints)
    if model is not None:
        print("The identified model is:")
        print(model)
        print()
        print("Use the following parameters to create packet for desired effect:")
        translated_model = st.translate_model(model)
        for k, v in translated_model.items():
            print(f"  {k}: {v}")
        print()
        rule = st.identify_rule(model)
        if rule:
            print(f"The iptabeles rule hit is:")
            print(rule)
        else:
            print("Something went wrong! Unable to identify associated rule /o\\")

    else:
        print("The provided constraints are not satisfiable.")


if __name__ == "__main__":
    main()
