from steem import Steem
from steem.post import Post
from steem.amount import Amount
import steembase.exceptions
import concurrent.futures
from datetime import datetime
import argparse
import json
import time
import logging
import threading
from dateutil.parser import parse


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logging.basicConfig()

mutex = threading.Semaphore()


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
        self.thread_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=config.get("threads"))
        self.main_post_title = config.get("main_post_title")
        self.main_post_tags = config.get("main_post_tags")
        self.main_post_template = open(
            config.get("main_post_template")).read()
        self.designated_post = self.main_post

    def url(self, p):
        return "https://steemit.com/@%s/%s" % (
            p.get("author"), p.get("permlink"))

    @property
    def main_post(self):
        today = datetime.utcnow().date().strftime("%Y-%m-%d")
        post_title = self.main_post_title.format(date=today)
        permlink = "last-minute-upvote-list-%s" % today

        try:
            return Post("%s/%s" % (self.bot_account, permlink))
        except steembase.exceptions.PostDoesNotExist:
            pass

        self.steemd_instance.commit.post(
            post_title,
            self.main_post_template,
            self.bot_account,
            tags=self.main_post_tags,
            permlink=permlink,
        )

        return Post("%s/%s" % (self.bot_account, permlink))

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
            return props['head_block_number']
        except TypeError:
            # sometimes nodes return null to that call.
            return self.get_last_block_height()

    def vote_abused(self, post, vote_created_at):
        diff = post["cashout_time"] - vote_created_at
        diff_in_hours = float(diff.total_seconds()) / float(3600)
        timeframe = list(map(int, self.timeframe.split("-")))
        return timeframe[0] < diff_in_hours < timeframe[1]

    def vote_value(self, vote_transaction, post):
        for active_vote in post.get("active_votes"):
            if active_vote["voter"] == vote_transaction["voter"]:
                payout = self.get_payout_from_rshares(
                    active_vote["rshares"]
                )
                return payout

    def handle_operation(self, op_type, op_value, timestamp, block_id):

        if op_type != "vote":
            # we're only interested in votes, skip.
            return

        comment_identifier = "@%s/%s" % (
            op_value["author"], op_value["permlink"])
        try:
            post = Post(comment_identifier)
        except steembase.exceptions.PostDoesNotExist:
            logger.info("Couldnt load the post. %s" % comment_identifier)
            return

        vote_created_at = parse(timestamp)

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
            target=self.broadcast_comment,
            args=(
                op_value["voter"],
                post,
                vote_value,
                vote_created_at,
            ))
        t.start()

    def broadcast_comment(self, voter, post, vote_value,
                          vote_created_at, retry_count=None):
        global mutex

        if not retry_count:
            retry_count = 0

        mutex.acquire()
        logger.info('Mutex acquired.')

        try:
            diff = post["cashout_time"] - vote_created_at
            diff_in_hours = float(diff.total_seconds()) / float(3600)

            comment_body = self.comment_template.format(
                username=voter,
                author=post.get("author"),
                description=post.get("permlink")[0:16],
                url=self.url(post),
                amount=round(vote_value, 4),
                time_remaining=round(diff_in_hours, 2),
                timeframe=self.timeframe,
                minimum_vote_value=self.minimum_vote_value
            )

            self.designated_post.reply(
                comment_body,
                author=self.bot_account,
            )

            time.sleep(20)
        except Exception as error:
            logger.error(error)
            if 'Duplicate' in error.args[0]:
                return
            if 'You may only comment once every' in error.args[0]:
                logger.error("Throttled for commenting. Sleeping.")
                time.sleep(20)
                return self.broadcast_comment(voter, post, vote_value,
                        vote_created_at, retry_count + 1)

            if retry_count < 10:
                return self.broadcast_comment(voter, post, vote_value,
                        vote_created_at, retry_count + 1)
            else:
                logger.error(
                    "Tried %s times to comment but failed. Giving up. %s",
                    retry_count,
                    post.identifier,
                )
        finally:
            logger.info('Mutex released.')
            mutex.release()

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
    args = parser.parse_args()
    config = json.loads(open(args.config).read())

    steemd_instance = Steem(
        nodes=config["nodes"],
        keys=[config["posting_key"], ]
    )
    sherlock = Sherlock(
        steemd_instance,
        config,
    )
    sherlock.run()


if __name__ == '__main__':
    main()
