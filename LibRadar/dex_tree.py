# -*- coding: utf-8 -*-
"""
    DEX Tree

    This script is used to implement the tree node and tree structure.
    :copyright: (c) 2016 by Zachary Ma
    : Project: LibRadar

"""

from _settings import *
import hashlib
import csv
import redis

# Databases
db_feature_count = redis.StrictRedis(host=DB_HOST, port=DB_PORT, db=DB_FEATURE_COUNT)
db_feature_weight = redis.StrictRedis(host=DB_HOST, port=DB_PORT, db=DB_FEATURE_WEIGHT)
db_un_ob_pn = redis.StrictRedis(host=DB_HOST, port=DB_PORT, db=DB_UN_OB_PN)
db_un_ob_pn_count = redis.StrictRedis(host=DB_HOST, port=DB_PORT, db=DB_UN_OB_PN_COUNT)

# tag_rules
labeled_libs = list()
no_lib = list()

with open(FILE_RULE, 'r') as file_rules:
    csv_rules_reader = csv.reader(file_rules, delimiter=',', quotechar='|')
    for row in csv_rules_reader:
        if row[1] == "no":
            no_lib.append(row)
        else:
            labeled_libs.append(row)


class TreeNode(object):
    """
    Tree Node Structure
    {
        md5     : 02b018f5b94c5fbc773ab425a15b8bbb              // In fact md5 is the non-hex one
        weight  : 1023                                          // How many APIs in this Node
        pn      : Lcom/facebook/internal                        // Current package name
        parent  : <TreeNode>                                    // Parent node
        children: dict("pn": <TreeNode>)                              // Children nodes
        match   : list( tuple(package_name, match_weight) )     // match lib list
    }
    """
    def __init__(self, n_weight=-1, n_pn="", n_parent=None):
        self.md5 = ""
        self.weight = n_weight
        self.pn = n_pn
        self.parent = n_parent
        self.children = dict()
        self.match = list()

    def insert(self, package_name, weight, md5):
        current_depth = 0 if self.pn == "" else self.pn.count('/') + 1
        target_depth = package_name.count('/') + 1
        if current_depth == target_depth:
            self.md5 = md5
            return "F: %s" % package_name
        target_package_name = '/'.join(package_name.split('/')[:current_depth + 1])
        if target_package_name in self.children:
            self.children[target_package_name].weight += weight
            return self.children[target_package_name].insert(package_name, weight, md5)
        else:
            self.children[target_package_name] = TreeNode(n_weight=weight, n_pn=target_package_name, n_parent=self)
            return self.children[target_package_name].insert(package_name, weight, md5)


class Tree(object):
    """
    Tree
    """
    def __init__(self):
        self.root = TreeNode()

    def insert(self, package_name, weight, md5):
        self.root.insert(package_name, weight, md5)

    def pre_order(self, visit):
        self._pre_order(self.root, visit)

    def _pre_order(self, node, visit):
        ret = visit(node)
        if ret < 0:
            return
        else:
            for child_pn in node.children:
                self._pre_order(node.children[child_pn], visit)

    def post_order(self, visit):
        self._post_order(self.root, visit)

    def _post_order(self, node, visit):
        for child_pn in node.children:
            self._post_order(node.children[child_pn], visit)
        visit(node)

    @staticmethod
    def _cal_md5(node):
        # Ignore Leaf Node
        if len(node.children) == 0 and node.md5 != "":
            return
        # Everything seems Okay.
        cur_md5 = hashlib.md5()
        md5_list = list()
        for child in node.children:
            md5_list.append(node.children[child].md5)
        md5_list.sort()
        for md5_item in md5_list:
            cur_md5.update(md5_item)
        node.md5 = cur_md5.digest()
        # you could see node.pn here. e.g. Lcom/tencent/mm/sdk/modelpay

    def cal_md5(self):
        """
        Calculate md5 for every package
        :return:
        """
        self.post_order(visit=self._cal_md5)

    @staticmethod
    def _match(node):
        a = db_un_ob_pn.get(node.md5)
        c = db_feature_count.get(node.md5)
        u = db_un_ob_pn_count.get(node.md5)
        """ Debug Log
        if a is not None:
            print "----"
            print "Potential Name: " + a
            print "Package Name  :" + node.pn
            print "Count: " + u + '/' + c
            print str(node.weight) + " " + str(w)
        """
        # if could not find this package in database, search its children.
        if a is None:
            return 1
        # Potential Name is not convincing enough.
        if u < 8 or float(u) / float(c) < 0.3:
            return 2
        flag_not_deeper = False
        for lib in labeled_libs:
            # if the potential package name is the same as full lib path
            # do not search its children
            if lib[0] == a:
                node.match.append([lib, node.weight])
                continue
            # If they have the same length but not equal to each other, just continue
            if len(lib[0]) == len(a):
                continue
            # if the potential package name is part of full lib path, search its children
            #   e.g. a is Lcom/google, we could find it as a part of Lcom/google/android/gms, so search its children for
            #       more details
            if len(a) < len(lib[0]) and a == lib[0][:len(a)]:
                continue
            # If the lib path is part of potential package name, add some count into parent's match list.
            if len(a) > len(lib[0]) and lib[0] == a[:len(lib[0])]:
                depth_diff = a.count('/') - lib[0].count('/')
                cursor = node
                for i in range(depth_diff):
                    # cursor should not be the root, so cursor's parent should not be None.
                    if cursor.parent.parent is not None:
                        cursor = cursor.parent
                    else:
                        # root's parent is None
                        #   This situation exists
                        #   For Example: If it takes Lcom/a/b as Lcom/google/android/gms/ads/mediation/customevent,
                        #   It will find its ancestor until root or None.
                        return 4
                flag = False
                for matc in cursor.match:
                    # if matc[0][0] == lib[0]:
                    if matc[0] == lib:
                        flag = True
                        if matc[1] != cursor.weight:
                            matc[1] += node.weight
                if not flag:
                    cursor.match.append([lib, node.weight])
                flag_not_deeper = True
                continue
        """
            One degree deeper!
            深入探测一层

                There's a situation that a package is a library and the child of a package is also a library.
                库是存在相互嵌套的。

                As we all know that Lcom/unity3d is definitely a Game Engine library. There could be some sub-package
                like Lcom/unity3d/player, Lcom/unity3d/plugin, Lcom/unity3d/sdk, etc. So we take Lcom/unity3d as the
                root package of this library.
                比如，Lcom/unity3d 显然是Unity3D这个游戏引擎，在游戏引擎下可能会有player, plugin, sdk等次级包（文件夹），所以我们很
                显然地把Lcom/unity3d作为游戏引擎的根包。

                However, Lcom/unity3d/ads is an Advertisement library.
                但是，Lcom/unity3d/ads是Unity3D公司推出的广告库

                If we do not search one degree deeper, we could only find the game engine other than the ads library.
                Likewise, we could not find Landroid/support/v4 anymore if we take Landroid/support as a library.
                如果我们不继续搜索的话，那么对于一个应用，我们只能检测到Unity3D这个引擎，无法检测到Unity3D Ads这个广告库。

            Implementation:
            实现：
                if lib[0] == a, we continue search his children.
                if lib[0] == a 这个后面从return变成了continue，我们会继续搜索它的子节点

                if we already found his child, we will not search deeper.
                在后面的代码中，如果已经知道的就是子节点，那么就不会继续深层的搜了。

                In my original code, I found a bug that the match degree is larger than the total amount of weight.
                This is impossible. After debugging, I found that if I add the match value multiple times, the match
                weight could overflow.
                在我原来有bug的代码中，我发现匹配的similarity有大于1的情况，即com/facebook这个库的similarity大于了1。这是因为match
                被我加总了数次

                For example:
                    There's a library Lcom/google/android/gson, weight is 189
                    we found Lcom/google/android/gson, so add the weight 189
                    we found Lcom/google/android/gson/internal, so add the weight 24
                    we found Lcom/google/android/gson/stream, so add the weight 43
                    In this case, the weight of package gson overflows.
                举例来看：
                    对于Lcom/google/android/gson这个包来说，它的API数量是189
                    搜索中找到 Lcom/google/android/gson， weight加上189
                    搜索中找到 Lcom/google/android/gson/internal， weight加上24
                    搜索中找到 Lcom/google/android/gson/stream, weight加上 43
                    这样显然就溢出了。

                Because we only search 1 degree deeper, the match situation of Lcom/google/android/gson is only true or
                false. In this case, we just need to check if the weight has overflowed before add weight. as the code:
                    if matc[1] != cursor.weight:
                        matc[1] += node.weight
                因为我们可以多搜一层，所以判断是否溢出很简单。因为对于上层的库来说，也就只有两种情况，那就是匹配到和没匹配到。所以只需要
                检测一下是否已经超出就行了。
        """
        if flag_not_deeper:
            return -1
        # Never find a good match, search its children.
        return 5

    def match(self):
        self.pre_order(visit=self._match)

    @staticmethod
    def _find_untagged(node):
        # If there's already some matches here, do not search its children. non-sense.
        if len(node.match) != 0:
            return -1
        a = db_un_ob_pn.get(node.md5)
        c = db_feature_count.get(node.md5)
        u = db_un_ob_pn_count.get(node.md5)
        # If the package name is already in no_lib list, ignore it and search its children.
        for non_lib in no_lib:
            if non_lib[0] == a:
                return 1
        # Potential Name is not convincing enough. search its children
        if u < 100 or float(u) / float(c) < 0.5 or node.weight < 50 or int(c) < 20:
            return 2
        print("----")
        print("Package: %s" % node.pn)
        print("Match Package: %s" % u)
        print("Library: Unknown.")
        print("Popularity: %s" % c)
        print("API count: %s" % node.weight)

    def find_untagged(self):
        self.pre_order(visit=self._find_untagged)
        print("==========================")

    @staticmethod
    def _get_lib(node):
        for matc in node.match:
            print("----")
            print("Package: %s" % node.pn)
            print("Library: %s" % matc[0][1])
            print("Standard Package: %s" % matc[0][0])
            print("Type: %s" % matc[0][2])
            print("Website: %s" % matc[0][3])
            print("Similarity: %d/%d" % (matc[1], node.weight))
        return 0

    def get_lib(self):
        print("\n===== RESULT: ============")
        self.pre_order(visit=self._get_lib)
        print("==========================")