import argparse
import concurrent.futures
import json
import logging
import threading
import time
from datetime import datetime, timedelta

import steembase.exceptions
from dateutil.parser import parse
from steem import Steem
from steem.amount import Amount
from steem.post import Post
from steem.account import Account

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logging.basicConfig()

mutex = threading.Semaphore()
reply_mutex = threading.Semaphore()
flag_mutex = threading.Semaphore()


class memoized:
    def __init__(self, ttl=2):
        self.cache = {}
        self.ttl = ttl

    def __call__(self, func):
        def _memoized(*args):
            self.func = func
            now = time.time()
            try:
                value, last_update = self.cache[args]
                age = now - last_update
                if age > self.ttl:
                    raise AttributeError

                return value

            except (KeyError, AttributeError):
                value = self.func(*args)
                self.cache[args] = (value, now)
                return value

            except TypeError:
                return self.func(*args)
        return _memoized


class Sherlock:

    def __init__(self, steemd_instance, config):
        self.steemd_instance = steemd_instance
        self.bot_account = config["bot_account"]
        self.start_block = config.get("start_block") or None
        self.timeframe = config.get("timeframe")
        self.minimum_vote_value = config.get("minimum_vote_value")
        self.comment_template = open(config.get("comment_template")).read()
        if config.get("reply_template"):
            self.reply_template = open(config.get("reply_template")).read()
        else:
            self.reply_template = None
        self.thread_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=config.get("threads"))
        self.main_post_title = config.get("main_post_title")
        self.main_post_tags = config.get("main_post_tags")
        self.main_post_template = open(
            config.get("main_post_template")).read()
        self.flag_options = config.get("flag_options")
        self.suspicious_users = config.get("suspicious_users")
        self.suspicious_users_timeframe = config.get(
            "suspicious_users_timeframe")
        self.whitelisted_users = []
        if config.get("whitelisted_users") and \
                isinstance(config.get("whitelisted_users"), list):
            self.whitelisted_users = config.get("whitelisted_users")
        self.self_voter_report_options = config.get("self_voter_report_options")
        self.account_for_flag_report = config.get("account_for_flag_report") or self.bot_account
        self.flag_report_options = config.get(
            "flag_report_options")

    def url(self, p):
        return "https://steemit.com/@%s/%s" % (
            p.get("author"), p.get("permlink"))

    @property
    def designated_post_for_self_vote_report(self):
        today = datetime.utcnow().date().strftime("%Y-%m-%d")
        post_title = self.self_voter_report_options.get("title").format(date=today)
        permlink = "self-voter-list-%s" % today

        try:
            return Post(
                "%s/%s" % (self.bot_account, permlink),
                steemd_instance=self.steemd_instance,
            )
        except steembase.exceptions.PostDoesNotExist:
            pass

        try:
            self.steemd_instance.commit.post(
                post_title,
                open(self.self_voter_report_options.get("post_template")).read(),
                self.bot_account,
                tags=self.self_voter_report_options.get("tags"),
                permlink=permlink,
            )
        except Exception as e:
            if 'You may only post once every 5 minutes' in e.args[0]:
                logger.info("Sleeping for 300 seconds to create a new post.")
                time.sleep(300)
                return self.designated_post_for_self_vote_report
            raise

        return Post(
            "%s/%s" % (self.bot_account, permlink),
            steemd_instance=self.steemd_instance
        )

    @property
    def designated_post(self):
        today = datetime.utcnow().date().strftime("%Y-%m-%d")
        post_title = self.main_post_title.format(date=today)
        permlink = "last-minute-upvote-list-%s" % today

        try:
            return Post(
                "%s/%s" % (self.bot_account, permlink),
                steemd_instance=self.steemd_instance,
            )
        except steembase.exceptions.PostDoesNotExist:
            pass

        try:
            self.steemd_instance.commit.post(
                post_title,
                self.main_post_template,
                self.bot_account,
                tags=self.main_post_tags,
                permlink=permlink,
            )
        except Exception as e:
            if 'You may only post once every 5 minutes' in e.args[0]:
                logger.info("Sleeping for 300 seconds to create a new post.")
                time.sleep(300)
                return self.designated_post
            raise

        return Post(
            "%s/%s" % (self.bot_account, permlink),
            steemd_instance=self.steemd_instance
        )

    def post_daily_flag_report(self):
        options = self.flag_report_options
        flags, total_amount = self.get_latest_flags()
        today = datetime.utcnow().date().strftime("%Y-%m-%d")
        post_title = options.get('title').format(date=today)
        permlink = "flag-report-%s" % today

        incidents = ""
        for author, flag in flags.items():
            incidents += "|@%s|%s|%s|$%s|\n" % (
                author,
                flag.get("posts"),
                flag.get("comments"),
                str(round(flag.get("total_removed"), 2)).replace("-", ""),
            )
        template = open(options.get("post_template")).read()
        body = template.format(
            total_amount=str(total_amount).replace("-", ""),
            incidents=incidents
        )
        print(body)
        try:
            self.steemd_instance.commit.post(
                post_title,
                body,
                self.bot_account,
                tags=options.get('tags'),
                permlink=permlink,
            )
        except Exception as e:
            if 'You may only post once every 5 minutes' in e.args[0]:
                logger.info("Sleeping for 300 seconds to create a new post.")
                time.sleep(300)
                return self.post_daily_flag_report()
            raise

    def get_latest_flags(self):
        flags = {}
        total_amount = 0
        account = Account(
            self.account_for_flag_report,
            steemd_instance=self.steemd_instance)
        for vote in account.history_reverse(filter_by="vote"):
            if vote["weight"] > 0:
                continue
            if vote["voter"] != self.account_for_flag_report:
                continue
            ts = parse(vote["timestamp"])
            if ts < (datetime.utcnow() - timedelta(days=1)):
                break

            try:
                p = Post(
                    "%s/%s" % (vote["author"], vote["permlink"]),
                    steemd_instance=self.steemd_instance)
            except steembase.exceptions.PostDoesNotExist:
                logger.info("Couldnt load the post. %s" % vote["permlink"])
                continue

            if vote["author"] not in flags:
                flags[vote.get("author")] = {"posts": 0, "comments": 0, "total_removed": 0}

            if p.is_main_post():
                flags[vote.get("author")].update({
                    "posts": flags[vote.get("author")]["posts"] + 1
                })
            else:
                flags[vote.get("author")].update({
                    "comments": flags[vote.get("author")]["comments"] + 1
                })

            logger.info("Analyzing %s" % self.url(p))

            for active_vote in p.get("active_votes"):
                if float(active_vote.get("rshares")) > 0:
                    continue

                if active_vote.get("voter") != self.account_for_flag_report:
                    continue

                amount_removed = self.get_payout_from_rshares(active_vote.get("rshares"))
                total_amount += amount_removed

                flags[vote.get("author")].update({
                    "total_removed": flags[vote.get("author")]["total_removed"] + amount_removed,
                })

        return flags, round(total_amount, 2)

    @memoized(ttl=300)
    def get_state(self):
        base_price = Amount(self.steemd_instance.\
            get_current_median_history_price()["base"]).amount
        reward_fund = self.steemd_instance.get_reward_fund('post')

        return base_price, reward_fund

    def get_payout_from_rshares(self, rshares):

        base_price, reward_fund = self.get_state()

        fund_per_share = Amount(
            reward_fund["reward_balance"]).amount / float(
            reward_fund["recent_claims"]
        )

        if isinstance(rshares, str):
            rshares = int(rshares)

        try:
            payout = rshares * fund_per_share * base_price
        except Exception as error:
            logger.error(error)

            raise

        return payout

    def get_last_block_height(self):
        try:
            props = self.steemd_instance.get_dynamic_global_properties()
            return props['last_irreversible_block_num']
        except (TypeError, steembase.exceptions.RPCError):
            # sometimes nodes return null to that call.
            return self.get_last_block_height()

    def vote_abused(self, post, vote_created_at):
        diff = post["cashout_time"] - vote_created_at
        diff_in_hours = float(diff.total_seconds()) / float(3600)
        timeframe = list(map(int, self.timeframe.split("-")))
        if self.suspicious_users and self.suspicious_users_timeframe:
            if post.get("author") in self.suspicious_users:
                timeframe = list(map(
                    int, self.suspicious_users_timeframe.split("-")))

        return timeframe[0] < diff_in_hours < timeframe[1]

    def vote_value(self, vote_transaction, post):
        for active_vote in post.get("active_votes"):
            if active_vote["voter"] == vote_transaction["voter"]:
                payout = self.get_payout_from_rshares(
                    active_vote["rshares"]
                )
                return payout

    def handle_self_vote(self, post, op_value, vote_created_at):
        if not self.self_voter_report_options:
            return

        if post.get("author") != op_value.get("voter"):
            return

        vote_value = self.vote_value(op_value, post)
        if vote_value < self.self_voter_report_options.get("minimum_vote_value"):
            return

        logger.info(
            "Found a self-vote: %s - voter: %s",
            self.url(post),
            op_value["voter"],
        )

        t = threading.Thread(
            target=self.edit_self_vote_main_post,
            args=(
                op_value["voter"],
                post,
                vote_value,
                vote_created_at,
            ))
        t.start()

    def handle_operation(self, op_type, op_value, timestamp, block_id):

        if op_type != "vote":
            # we're only interested in votes, skip.
            return

        comment_identifier = "@%s/%s" % (
            op_value["author"], op_value["permlink"])

        if op_value["voter"] in self.whitelisted_users:
            logger.info("%s is whitelisted. Skipping.", op_value["voter"])
            return

        try:
            post = Post(
                comment_identifier,
                steemd_instance=self.steemd_instance)
        except steembase.exceptions.PostDoesNotExist:
            logger.info("Couldnt load the post. %s" % comment_identifier)
            return

        vote_created_at = parse(timestamp)

        # handle self-vote
        self.handle_self_vote(post, op_value, vote_created_at)

        # check the timeframe
        if not self.vote_abused(post, vote_created_at):
            # no abuse here, move on.
            return

        # check the vote value
        vote_value = self.vote_value(op_value, post)
        if vote_value < self.minimum_vote_value:
            return

        logger.info(
            "Found an incident: %s - voter: %s, block id: %s",
            self.url(post),
            op_value["voter"],
            block_id
        )

        t = threading.Thread(
            target=self.edit_main_post,
            args=(
                op_value["voter"],
                post,
                vote_value,
                vote_created_at,
            ))
        t.start()

    def edit_self_vote_main_post(self, voter, post, vote_value,
                          vote_created_at, retry_count=None):
        global mutex

        if not retry_count:
            retry_count = 0

        mutex.acquire()
        logger.info('Post edit mutex acquired.')

        try:

            incident_body = "|@{author}|[link]({url})|**${amount}**|\n"
            incident_body = incident_body.format(
                author=post.get("author"),
                url=self.url(post),
                amount=round(vote_value, 2),
            )

            self.designated_post_for_self_vote_report.edit(
                self.designated_post_for_self_vote_report.body + incident_body,
            )

            time.sleep(20)
        except Exception as error:
            logger.error(error)
            if 'Duplicate' in error.args[0]:
                mutex.release()
                return
            if 'You may only comment once every' in error.args[0]:
                logger.error("Throttled for commenting. Sleeping.")
                time.sleep(20)
                mutex.release()
                return self.edit_self_vote_main_post(voter, post, vote_value,
                        vote_created_at, retry_count + 1)

            if retry_count < 10:
                mutex.release()
                return self.edit_self_vote_main_post(voter, post, vote_value,
                        vote_created_at, retry_count + 1)
            else:
                logger.error(
                    "Tried %s times to comment but failed. Giving up. %s",
                    retry_count,
                    post.identifier,
                )
        finally:
            logger.info('Post edit mutex released.')
            mutex.release()

    def edit_main_post(self, voter, post, vote_value,
                          vote_created_at, retry_count=None):
        global mutex

        if not retry_count:
            retry_count = 0

        mutex.acquire()
        logger.info('Post edit mutex acquired.')

        try:
            diff = post["cashout_time"] - vote_created_at
            diff_in_hours = float(diff.total_seconds()) / float(3600)

            comment_body = self.comment_template.format(
                username=voter,
                author=post.get("author"),
                description=post.get("permlink")[0:16],
                url=self.url(post),
                amount=round(vote_value, 2),
                time_remaining=round(diff_in_hours, 2),
                timeframe=self.timeframe,
                minimum_vote_value=self.minimum_vote_value
            )

            self.designated_post.edit(
                self.designated_post.body + comment_body,
            )
            if self.reply_template:
                # send reply to voted post
                t = threading.Thread(
                    target=self.send_reply,
                    args=(
                        voter,
                        post,
                        vote_value,
                        diff_in_hours,
                    ))
                t.start()

            if self.flag_options:
                t = threading.Thread(
                    target=self.flag,
                    args=(post, )
                )

                t.start()

            time.sleep(20)
        except Exception as error:
            logger.error(error)
            if 'Duplicate' in error.args[0]:
                mutex.release()
                return
            if 'You may only comment once every' in error.args[0]:
                logger.error("Throttled for commenting. Sleeping.")
                time.sleep(20)
                mutex.release()
                return self.edit_main_post(voter, post, vote_value,
                        vote_created_at, retry_count + 1)

            if retry_count < 10:
                mutex.release()
                return self.edit_main_post(voter, post, vote_value,
                        vote_created_at, retry_count + 1)
            else:
                logger.error(
                    "Tried %s times to comment but failed. Giving up. %s",
                    retry_count,
                    post.identifier,
                )
        finally:
            logger.info('Post edit mutex released.')
            mutex.release()

    def send_reply(self, voter, post, vote_value, diff_in_hours,
                   retry_count=None):
        global reply_mutex

        if not self.reply_template:
            logger.info("Reply template isn't set. Skipping replies.")
            return

        if not retry_count:
            retry_count = 0

        try:
            reply_mutex.acquire()
            logger.info('Reply mutex acquired.')

            reply_body = self.reply_template.format(
                voter=voter,
                author=post.get("author"),
                amount=round(vote_value, 4),
                time_remaining=round(diff_in_hours, 2),
            )
            post.reply(reply_body, author=self.bot_account)
            time.sleep(20)
        except Exception as error:
            logger.error(error)
            if 'Duplicate' in error.args[0]:
                reply_mutex.release()
                return
            if 'You may only comment once every' in error.args[0]:
                logger.error("Throttled for commenting. Sleeping.")
                time.sleep(20)
                logger.error("Sleep is finished, trying again.")
                reply_mutex.release()
                return self.send_reply(voter, post, vote_value,
                        diff_in_hours, retry_count + 1)

            if retry_count < 10:
                reply_mutex.release()
                logger.error("retry count is below 10, trying again.")
                return self.send_reply(voter, post, vote_value,
                        diff_in_hours, retry_count + 1)
            else:
                logger.error(
                    "Tried %s times to comment but failed. Giving up. %s",
                    retry_count,
                    post.identifier,
                )

        finally:
            reply_mutex.release()
            logger.info('Reply mutex released.')

    def flag(self, post, retry_count=0):
        global flag_mutex

        if not retry_count:
            retry_count = 0

        try:
            flag_mutex.acquire()
            logger.info("Flag mutex acquired.")

            weight = self.flag_options.get("weight") or -1
            voter = self.flag_options.get("from_account")

            self.steemd_instance.commit.vote(
                post.identifier,
                weight,
                account=voter)
            logger.info("Flagged: %s.", post.identifier)
            time.sleep(3)

        except Exception as error:
            logger.error(error)

            if retry_count < 5:
                flag_mutex.release()
                return self.flag(post, retry_count + 1)
            else:
                logger.info(
                    "Tried 5 times to flag: %s. Failed. Skipping.",
                    post.identifier
                )
                flag_mutex.release()
                return

        finally:
            flag_mutex.release()
            logger.info("Flag mutex released.")

    def parse_block(self, block_id):
        logger.info("Parsing %s", block_id)

        # get all operations in the related block id
        operation_data = self.steemd_instance.get_ops_in_block(
            block_id, virtual_only=False)

        block_header = self.steemd_instance.get_block_header(block_id)

        for operation in operation_data:
            self.handle_operation(
                operation['op'][0],
                operation['op'][1],
                block_header["timestamp"],
                block_id,
            )

    def run(self):
        if not self.start_block:
            starting_point = self.get_last_block_height()
        while True:
            while (self.get_last_block_height() - starting_point) > 0:
                starting_point += 1
                self.thread_pool.submit(self.parse_block, starting_point)
            time.sleep(3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config", help="Config file in JSON format")
    parser.add_argument("--post-daily-flag-report", help="Posts daily flag report")
    args = parser.parse_args()
    config = json.loads(open(args.config).read())

    keys = [config.get("posting_key")]
    if config.get("flag_options") and \
            'from_account_posting_key' in config.get("flag_options"):
        keys.append(config["flag_options"]["from_account_posting_key"])


    steemd_instance = Steem(
        nodes=config["nodes"],
        keys=keys,
    )

    sherlock = Sherlock(
        steemd_instance,
        config,
    )
    if args.post_daily_flag_report:
        sherlock.post_daily_flag_report()
        return

    sherlock.run()


if __name__ == '__main__':
    main()
